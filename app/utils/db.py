import os
import time
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))


# ===== 共用工具 =====
def _latest_rows(table, stock_id, order_cols, limit):
    """
    取某檔股票「最近」的 limit 筆資料，回傳時依時間由舊到新排列。
    （先由新到舊取 limit 筆，再反轉；直接由舊到新取會拿到最早的資料）
    """
    query = supabase.table(table).select("*").eq("stock_id", stock_id)
    for col in order_cols:
        query = query.order(col, desc=True)
    res = query.limit(limit).execute()
    df = pd.DataFrame(res.data)
    if df.empty:
        return df
    return df.sort_values(order_cols).reset_index(drop=True)


def _fetch_all(make_query, page_size=1000):
    """分頁取回查詢的全部資料。make_query 每次呼叫要回傳一個新的查詢。"""
    rows = []
    start = 0
    while True:
        res = make_query().range(start, start + page_size - 1).execute()
        if not res.data:
            break
        rows.extend(res.data)
        if len(res.data) < page_size:
            break
        start += page_size
    return rows


# ===== 找最新日期 =====
# 各表只有 (stock_id, 日期) 主鍵，沒有單獨的日期索引。直接 order by 日期 要掃整張表
# （約 2 秒，同時查幾個就會超過 Supabase 3 秒的 statement timeout）。
# 改查幾檔每個交易日都有資料的權值股，走主鍵索引，不到 0.1 秒。
_ANCHOR_STOCKS = ["2330", "2317", "2454"]  # 台積電、鴻海、聯發科

def get_recent_dates(table, date_col="date", n=1):
    """取資料表最近 n 個有資料的日期，由新到舊排列"""
    res = supabase.table(table).select(date_col) \
        .in_("stock_id", _ANCHOR_STOCKS) \
        .order(date_col, desc=True) \
        .limit(n * len(_ANCHOR_STOCKS)) \
        .execute()
    dates = sorted({r[date_col] for r in res.data}, reverse=True)[:n]
    if dates:
        return dates
    # 權值股都查不到（不太可能發生）時，退回掃整張表找最新一天
    res = supabase.table(table).select(date_col) \
        .order(date_col, desc=True).limit(1).execute()
    return [res.data[0][date_col]] if res.data else []

def get_latest_date(table, date_col="date"):
    dates = get_recent_dates(table, date_col, n=1)
    return dates[0] if dates else None

# ===== 基本資料 =====
_STOCK_LIST_TTL = 3600  # 股票清單快取一小時，產業別、新上市櫃更新後不必重啟程式
_stock_list_cache = {"time": 0.0, "data": None}

def get_stock_list():
    now = time.time()
    if _stock_list_cache["data"] is not None and now - _stock_list_cache["time"] < _STOCK_LIST_TTL:
        return _stock_list_cache["data"]
    df = _load_stock_list()
    _stock_list_cache.update(time=now, data=df)
    return df

def _clear_stock_list_cache():
    _stock_list_cache.update(time=0.0, data=None)

get_stock_list.cache_clear = _clear_stock_list_cache  # 沿用原本 lru_cache 的介面

def _load_stock_list():
    all_data = []
    start = 0
    while True:
        res = supabase.table("stock_info").select("*") \
            .order("stock_id", desc=False) \
            .range(start, start + 999).execute()
        if not res.data:
            break
        all_data.extend(res.data)
        if len(res.data) < 1000:
            break
        start += 1000
    return pd.DataFrame(all_data)

def get_stock_info(stock_id):
    res = supabase.table("stock_info").select("*").eq("stock_id", stock_id).execute()
    return res.data[0] if res.data else None

# ===== 股價 =====
def get_daily_prices(stock_id, limit=180):
    return _latest_rows("daily_prices", stock_id, ["date"], limit)

def get_latest_prices():
    latest_date = get_latest_date("daily_prices", "date")
    if latest_date is None:
        return pd.DataFrame()
    all_data = []
    start = 0
    while True:
        r = supabase.table("daily_prices") \
            .select("stock_id, date, open_price, high_price, low_price, close_price, volume") \
            .gte("date", latest_date) \
            .order("stock_id", desc=False) \
            .range(start, start + 999) \
            .execute()
        if not r.data:
            break
        all_data.extend(r.data)
        if len(r.data) < 1000:
            break
        start += 1000
    if not all_data:
        return pd.DataFrame()
    df = pd.DataFrame(all_data)
    df = df.sort_values("date", ascending=False)
    df = df.drop_duplicates(subset="stock_id", keep="first")
    return df

def get_recent_closes(calendar_days=40):
    """
    取所有股票最近 calendar_days 天（日曆天）的收盤價，給總覽頁算 RSI 用。
    40 個日曆天約 25 個交易日，遇到長假也夠算 RSI(14)。
    """
    latest_date = get_latest_date("daily_prices", "date")
    if latest_date is None:
        return pd.DataFrame()
    latest_date = pd.to_datetime(latest_date)
    # 一天一個查詢（走主鍵索引，每次只要幾十毫秒），少量同時送出；週末不會有資料，直接略過
    days = [
        d.strftime("%Y-%m-%d")
        for d in pd.date_range(end=latest_date, periods=calendar_days + 1)
        if d.weekday() < 5
    ]

    def fetch_day(day):
        return _fetch_all(
            lambda: supabase.table("daily_prices")
                .select("stock_id, date, close_price")
                .eq("date", day)
                .order("stock_id", desc=False)
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = [r for day_rows in pool.map(fetch_day, days) for r in day_rows]
    return pd.DataFrame(rows)

# ===== 三大法人 =====
def get_stock_ins(stock_id, limit=60):
    return _latest_rows("stock_ins_daily", stock_id, ["date"], limit)

def get_latest_ins():
    latest_date = get_latest_date("stock_ins_daily", "date")
    if latest_date is None:
        return pd.DataFrame()
    all_data = []
    start = 0
    while True:
        r = supabase.table("stock_ins_daily") \
            .select("*") \
            .eq("date", latest_date) \
            .order("stock_id", desc=False) \
            .range(start, start + 999) \
            .execute()
        if not r.data:
            break
        all_data.extend(r.data)
        if len(r.data) < 1000:
            break
        start += 1000
    return pd.DataFrame(all_data)

# ===== 融資融券 =====
def get_margin(stock_id, limit=60):
    return _latest_rows("margin_trading", stock_id, ["date"], limit)

def get_latest_margin():
    latest_date = get_latest_date("margin_trading", "date")
    if latest_date is None:
        return pd.DataFrame()
    all_data = []
    start = 0
    while True:
        r = supabase.table("margin_trading") \
            .select("*") \
            .eq("date", latest_date) \
            .order("stock_id", desc=False) \
            .range(start, start + 999) \
            .execute()
        if not r.data:
            break
        all_data.extend(r.data)
        if len(r.data) < 1000:
            break
        start += 1000
    return pd.DataFrame(all_data)

# ===== 融資融券使用率 =====
def get_margin_utilization(stock_id, limit=60):
    """取得單一股票融資融券使用率歷史"""
    return _latest_rows("margin_utilization", stock_id, ["date"], limit)

def get_latest_margin_utilization():
    """取得所有股票最新融資融券使用率"""
    latest_date = get_latest_date("margin_utilization", "date")
    if latest_date is None:
        return pd.DataFrame()
    all_data = []
    start = 0
    while True:
        r = supabase.table("margin_utilization") \
            .select("*") \
            .eq("date", latest_date) \
            .order("stock_id", desc=False) \
            .range(start, start + 999) \
            .execute()
        if not r.data:
            break
        all_data.extend(r.data)
        if len(r.data) < 1000:
            break
        start += 1000
    return pd.DataFrame(all_data)

# ===== 大戶持股 =====
def get_holder(stock_id, limit=20):
    return _latest_rows("holder_distribution", stock_id, ["week_date"], limit)

def get_latest_holder():
    latest_date = get_latest_date("holder_distribution", "week_date")
    if latest_date is None:
        return pd.DataFrame()
    all_data = []
    start = 0
    while True:
        r = supabase.table("holder_distribution") \
            .select("*") \
            .eq("week_date", latest_date) \
            .order("stock_id", desc=False) \
            .range(start, start + 999) \
            .execute()
        if not r.data:
            break
        all_data.extend(r.data)
        if len(r.data) < 1000:
            break
        start += 1000
    return pd.DataFrame(all_data)

# ===== 月營收 =====
def get_revenue(stock_id, limit=12):
    return _latest_rows("monthly_revenue", stock_id, ["year_month"], limit)

def get_latest_revenue():
    latest_ym = get_latest_date("monthly_revenue", "year_month")
    if latest_ym is None:
        return pd.DataFrame()
    all_data = []
    start = 0
    while True:
        r = supabase.table("monthly_revenue") \
            .select("*") \
            .eq("year_month", latest_ym) \
            .order("stock_id", desc=False) \
            .range(start, start + 999) \
            .execute()
        if not r.data:
            break
        all_data.extend(r.data)
        if len(r.data) < 1000:
            break
        start += 1000
    return pd.DataFrame(all_data)

# ===== 季報 =====
def get_quarterly(stock_id, limit=8):
    return _latest_rows("quarterly_report", stock_id, ["year", "quarter"], limit)

def get_latest_quarterly():
    """
    取得每檔股票最新一季的季報。
    上市與上櫃的出表日期不同，所以不能用出表日期判斷「最新」；
    這裡取最新與前一個報告期的資料，每檔股票只保留較新的一筆。
    （財報公布期間，尚未公布新一季的公司會顯示上一季）
    """
    res = supabase.table("quarterly_report") \
        .select("year, quarter") \
        .order("year", desc=True) \
        .order("quarter", desc=True) \
        .limit(1) \
        .execute()
    if not res.data:
        return pd.DataFrame()
    year, quarter = res.data[0]["year"], res.data[0]["quarter"]
    prev_year, prev_quarter = (year, quarter - 1) if quarter > 1 else (year - 1, 4)

    all_data = []
    for y, q in [(year, quarter), (prev_year, prev_quarter)]:
        all_data.extend(_fetch_all(
            lambda y=y, q=q: supabase.table("quarterly_report")
                .select("*")
                .eq("year", y)
                .eq("quarter", q)
                .order("stock_id", desc=False)
        ))
    if not all_data:
        return pd.DataFrame()
    df = pd.DataFrame(all_data)
    df = df.sort_values(["year", "quarter"], ascending=False)
    df = df.drop_duplicates(subset="stock_id", keep="first")
    return df.reset_index(drop=True)

# ===== 期貨三大法人 =====
def get_futures_ins(limit=60):
    """取最近 limit 個交易日（每天 3 種身分別），依日期由舊到新排列"""
    res = supabase.table("futures_institutional") \
        .select("*") \
        .eq("contract_code", "臺股期貨") \
        .order("date", desc=True) \
        .limit(limit * 3) \
        .execute()
    df = pd.DataFrame(res.data)
    if df.empty:
        return df
    return df.sort_values(["date", "item"]).reset_index(drop=True)
