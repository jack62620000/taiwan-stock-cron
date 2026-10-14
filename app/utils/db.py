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

# ===== 全市場某一天的資料 =====
def get_snapshot(table, date_col, value, columns="*"):
    """取全市場在某個日期（或年月、週）的資料，自動分頁"""
    return pd.DataFrame(_fetch_all(
        lambda: supabase.table(table)
            .select(columns)
            .eq(date_col, value)
            .order("stock_id", desc=False)
    ))

def get_snapshots(table, dates, columns="*", date_col="date"):
    """取全市場在多個日期的資料，依日期逐一查詢後合併"""
    frames = [get_snapshot(table, date_col, d, columns) for d in dates]
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

def _latest_snapshot(table, date_col="date", columns="*"):
    """取全市場最新一天的資料"""
    latest = get_latest_date(table, date_col)
    if latest is None:
        return pd.DataFrame()
    return get_snapshot(table, date_col, latest, columns)

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
    return pd.DataFrame(_fetch_all(
        lambda: supabase.table("stock_info").select("*").order("stock_id", desc=False)
    ))

def get_stock_info(stock_id):
    res = supabase.table("stock_info").select("*").eq("stock_id", stock_id).execute()
    return res.data[0] if res.data else None

# ===== 股價 =====
def get_daily_prices(stock_id, limit=180):
    return _latest_rows("daily_prices", stock_id, ["date"], limit)

def get_latest_prices():
    return _latest_snapshot(
        "daily_prices", "date",
        "stock_id, date, open_price, high_price, low_price, close_price, volume",
    )

def get_recent_closes(calendar_days=40, columns="stock_id, date, close_price"):
    """
    取所有股票最近 calendar_days 天（日曆天）的收盤價，給總覽頁算 RSI、葛蘭碧頁算均線用。
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
        return get_snapshot("daily_prices", "date", day, columns)

    with ThreadPoolExecutor(max_workers=3) as pool:
        frames = [f for f in pool.map(fetch_day, days) if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

# ===== 三大法人 =====
def get_stock_ins(stock_id, limit=60):
    return _latest_rows("stock_ins_daily", stock_id, ["date"], limit)

def get_latest_ins():
    return _latest_snapshot("stock_ins_daily")

# ===== 融資融券 =====
def get_margin(stock_id, limit=60):
    return _latest_rows("margin_trading", stock_id, ["date"], limit)

def get_latest_margin():
    return _latest_snapshot("margin_trading")

# ===== 融資融券使用率 =====
def get_margin_utilization(stock_id, limit=60):
    """取得單一股票融資融券使用率歷史"""
    return _latest_rows("margin_utilization", stock_id, ["date"], limit)

def get_latest_margin_utilization():
    """取得所有股票最新融資融券使用率"""
    return _latest_snapshot("margin_utilization")

# ===== 大戶持股 =====
def get_holder(stock_id, limit=20):
    return _latest_rows("holder_distribution", stock_id, ["week_date"], limit)

def get_latest_holder():
    return _latest_snapshot("holder_distribution", "week_date")

# ===== 月營收 =====
def get_revenue(stock_id, limit=12):
    return _latest_rows("monthly_revenue", stock_id, ["year_month"], limit)

def get_latest_revenue():
    return _latest_snapshot("monthly_revenue", "year_month")

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
def get_futures_ins():
    """取臺股期貨三大法人全部歷史資料（自動分頁，不受單次 1000 筆上限影響），依日期由新到舊排列"""
    df = pd.DataFrame(_fetch_all(
        lambda: supabase.table("futures_institutional")
            .select("*")
            .eq("contract_code", "臺股期貨")
            .order("date", desc=True)
            .order("item", desc=False)
    ))
    return df.reset_index(drop=True)

# ===== 每日選股訊號 =====
def get_signal_dates(n=120):
    """daily_signals 最近 n 個評分日，由新到舊（表上有日期索引，直接查即可）"""
    res = supabase.table("daily_signals").select("date") \
        .in_("stock_id", _ANCHOR_STOCKS) \
        .order("date", desc=True) \
        .limit(n * len(_ANCHOR_STOCKS)) \
        .execute()
    return sorted({r["date"] for r in res.data}, reverse=True)[:n]

def get_signals(date):
    """某一個評分日全市場的評分"""
    return pd.DataFrame(_fetch_all(
        lambda: supabase.table("daily_signals").select("*")
            .eq("date", date)
            .order("stock_id", desc=False)
    ))

def get_signal_history(stock_id, limit=60):
    """單一股票最近 limit 個評分日的分數"""
    return _latest_rows("daily_signals", stock_id, ["date"], limit)

def get_signal_performance(top_n=20, horizon=5):
    """規則回測：每個評分日前 N 名持有 horizon 個交易日的平均報酬 vs 全市場平均（%）"""
    res = supabase.rpc("signal_performance", {"top_n": top_n, "horizon": horizon}).execute()
    return pd.DataFrame(res.data)
