import os
import time
from concurrent.futures import ThreadPoolExecutor
import httpx
import pandas as pd
from supabase import ClientOptions, create_client
from dotenv import load_dotenv

load_dotenv()
# 預設的 HTTP/2 是同一條連線多工，好幾個執行緒同時查詢時會互相卡住，偶爾還會出錯（KeyError、Server disconnected）。
# 改用 HTTP/1.1 連線池：每個執行緒各用一條連線，同時查詢才真的同時進行。
_http = httpx.Client(http2=False, timeout=120, follow_redirects=True, limits=httpx.Limits(max_connections=10))
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"), options=ClientOptions(httpx_client=_http))


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


def _execute(query, retries=2):
    """送出查詢；連線被 Supabase 中途關掉（Server disconnected 等）時重試，不讓整頁出錯"""
    for attempt in range(retries + 1):
        try:
            return query.execute()
        except httpx.TransportError:
            if attempt == retries:
                raise
            time.sleep(0.5 * (attempt + 1))


def _fetch_all(make_query, page_size=1000):
    """分頁取回查詢的全部資料。make_query 每次呼叫要回傳一個新的查詢。"""
    rows = []
    start = 0
    while True:
        res = _execute(make_query().range(start, start + page_size - 1))
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

_SNAPSHOT_WORKERS = 6  # 同時查幾天；每天一個查詢只要幾十毫秒，主要花在網路往返

def get_snapshots(table, dates, columns="*", date_col="date"):
    """取全市場在多個日期的資料，每天一個查詢、少量同時送出後合併"""
    with ThreadPoolExecutor(max_workers=_SNAPSHOT_WORKERS) as pool:
        frames = list(pool.map(lambda d: get_snapshot(table, date_col, d, columns), dates))
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

def get_stock_list(include_delisted=False):
    """股票清單。預設只回傳仍在交易的股票；要對照歷史資料的名稱時傳 include_delisted=True"""
    now = time.time()
    if _stock_list_cache["data"] is not None and now - _stock_list_cache["time"] < _STOCK_LIST_TTL:
        df = _stock_list_cache["data"]
    else:
        df = _load_stock_list()
        _stock_list_cache.update(time=now, data=df)
    if include_delisted or df.empty or "delisted_date" not in df.columns:
        return df
    return df[df["delisted_date"].isna()].reset_index(drop=True)

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
def get_daily_prices(stock_id, limit=180, adjust=True):
    """個股最近 limit 天股價；adjust=True 時回傳還原權值後的價格（見 apply_adjustments）"""
    df = _latest_rows("daily_prices", stock_id, ["date"], limit)
    if adjust and not df.empty:
        df = apply_adjustments(df, get_price_adjustments(stock_id, df["date"].min()))
    return df

def get_latest_prices():
    return _latest_snapshot(
        "daily_prices", "date",
        "stock_id, date, open_price, high_price, low_price, close_price, volume",
    )

def get_recent_closes(calendar_days=40, columns="stock_id, date, close_price", adjust=True):
    """
    取所有股票最近 calendar_days 天（日曆天）的收盤價，給總覽頁算 RSI、葛蘭碧頁算均線用。
    40 個日曆天約 25 個交易日，遇到長假也夠算 RSI(14)。
    adjust=True 時回傳還原權值後的價格（見 apply_adjustments）。
    """
    df = _recent_series("daily_prices", calendar_days, columns)
    if adjust and not df.empty:
        df = apply_adjustments(df, get_price_adjustments(None, df["date"].min()))
    return df

def get_recent_ins(calendar_days=10, columns="stock_id, date, foreign_net_shares"):
    """取所有股票最近 calendar_days 天（日曆天）的法人買賣超，給葛蘭碧頁的籌碼確認條件用"""
    return _recent_series("stock_ins_daily", calendar_days, columns)

# 資料庫函式（sql/functions.sql）一次回傳「每檔股票一列、近期每天的值放在陣列裡」，
# 約 2 千列，比逐日查全市場（十幾萬列）快很多。陣列欄位 → 原本的欄位名稱
_SERIES_FUNCTIONS = {
    "daily_prices": ("recent_prices", {"closes": "close_price", "volumes": "volume"}),
    "stock_ins_daily": ("recent_ins", {"foreign_net": "foreign_net_shares", "sitca_net": "sitca_net_shares"}),
}

def _recent_series(table, calendar_days, columns):
    """全市場最近 calendar_days 天（日曆天）的逐日資料，依日期、股票代號排序"""
    latest_date = get_latest_date(table, "date")
    if latest_date is None:
        return pd.DataFrame()
    fn, arrays = _SERIES_FUNCTIONS[table]
    wanted = [c.strip() for c in columns.split(",")]
    if not set(wanted) <= {"stock_id", "date", *arrays.values()}:
        return _recent_snapshots(table, latest_date, calendar_days, columns)  # 函式沒有的欄位改用逐日查詢
    start = (pd.to_datetime(latest_date) - pd.Timedelta(days=calendar_days)).strftime("%Y-%m-%d")
    try:
        rows = _fetch_all(lambda: supabase.rpc(fn, {"start_date": start}))
    except Exception:
        return _recent_snapshots(table, latest_date, calendar_days, columns)  # 函式還沒建立或暫時失敗
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).rename(columns={"dates": "date", **arrays})
    df = df.explode(["date", *arrays.values()], ignore_index=True)
    for col in arrays.values():
        df[col] = pd.to_numeric(df[col], errors="coerce")  # 展開後是 object，轉回數字
    return df.sort_values(["date", "stock_id"], ignore_index=True)[wanted]

def _recent_snapshots(table, latest_date, calendar_days, columns):
    """逐日查詢全市場資料（_recent_series 的備用做法）"""
    latest_date = pd.to_datetime(latest_date)
    # 週末不會有資料，直接略過
    days = [
        d.strftime("%Y-%m-%d")
        for d in pd.date_range(end=latest_date, periods=calendar_days + 1)
        if d.weekday() < 5
    ]
    return get_snapshots(table, days, columns)

# ===== 還原權值 =====
# price_adjustments 每筆事件（除權息、減資、變更面額）記錄 factor = 參考價 ÷ 事件前收盤價。
# 事件日之前的股價乘上之後所有事件的 factor，前後股價就能連續，均線、RSI、KD 等不會被假跳空扭曲。
# 最新一天的股價不受影響，和看盤軟體的「還原股價」相同。
_PRICE_COLS = ["open_price", "high_price", "low_price", "close_price"]
_VOLUME_EVENTS = ("變更面額",)  # 股數跟著等比例變動的事件，成交量也要還原

def get_price_adjustments(stock_id=None, after_date=None):
    """取還原權值事件；stock_id 為 None 時取全部股票。只取 after_date 之後的事件（之前的不影響）"""
    def make_query():
        q = supabase.table("price_adjustments").select("stock_id, date, event_type, factor")
        if stock_id is not None:
            q = q.eq("stock_id", stock_id)
        if after_date is not None:
            q = q.gt("date", str(after_date))
        return q.order("date", desc=False).order("stock_id", desc=False).order("event_type", desc=False)
    return pd.DataFrame(_fetch_all(make_query))

def apply_adjustments(prices, events):
    """
    把股價換成還原權值價格。prices 需有 stock_id、date 欄位，有哪些價格欄位就還原哪些；
    變更面額（例如面額 10 元改 1 元）的成交量也一併還原，讓量能指標前後可比。
    """
    if events is None or events.empty:
        return prices
    df = prices.copy()
    ev = events[["stock_id", "date", "event_type", "factor"]].copy()
    ev["factor"] = pd.to_numeric(ev["factor"], errors="coerce")
    ev = ev[ev["factor"] > 0]
    # 每列股價配上同一檔「之後」的所有事件，連乘就是這一列要乘的比例
    rows = df[["stock_id", "date"]].rename_axis("_row").reset_index().merge(ev, on="stock_id", suffixes=("", "_ev"))
    rows = rows[pd.to_datetime(rows["date"]) < pd.to_datetime(rows["date_ev"])]
    price_mult = rows.groupby("_row")["factor"].prod().reindex(df.index, fill_value=1.0)
    vol_rows = rows[rows["event_type"].isin(_VOLUME_EVENTS)]
    vol_mult = (1 / vol_rows.groupby("_row")["factor"].prod()).reindex(df.index, fill_value=1.0)
    for col in _PRICE_COLS:
        if col in df.columns:
            df[col] = (pd.to_numeric(df[col], errors="coerce") * price_mult).round(2)
    if "volume" in df.columns and (vol_mult != 1).any():
        df["volume"] = (pd.to_numeric(df["volume"], errors="coerce") * vol_mult).round()
    return df

# ===== 三大法人 =====
def get_stock_ins(stock_id, limit=60):
    return _latest_rows("stock_ins_daily", stock_id, ["date"], limit)

def get_latest_ins():
    return _latest_snapshot("stock_ins_daily")

# ===== 融資融券 =====
# 融資、融券餘額改讀 margin_utilization（與原 margin_trading 的餘額完全相同，省去重複的表）
def get_margin(stock_id, limit=60):
    return _latest_rows("margin_utilization", stock_id, ["date"], limit)

def get_latest_margin():
    return _latest_snapshot("margin_utilization")

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

# ===== 大盤指數 =====
def get_market_index(index_code="TAIEX", limit=250):
    """加權（TAIEX）或櫃買（TPEX）指數最近 limit 個交易日，由舊到新排列"""
    res = supabase.table("market_index").select("*").eq("index_code", index_code) \
        .order("date", desc=True).limit(limit).execute()
    df = pd.DataFrame(res.data)
    if df.empty:
        return df
    return df.sort_values("date").reset_index(drop=True)

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

def get_top_signals(date, n=5):
    """某一個評分日排名前 n 名（只含有排名的流動性足夠股票）"""
    res = supabase.table("daily_signals").select("*") \
        .eq("date", date).lte("rank", n).order("rank").execute()
    return pd.DataFrame(res.data)

def get_signal_history(stock_id, limit=60):
    """單一股票最近 limit 個評分日的分數"""
    return _latest_rows("daily_signals", stock_id, ["date"], limit)

def get_signal_performance(top_n=20, horizon=5):
    """規則回測：每個評分日前 N 名持有 horizon 個交易日的平均報酬 vs 全市場平均（%）"""
    # 資料庫快取是冷的時候，這個函式可能超過 Supabase API 的查詢逾時（anon 3 秒）；
    # 第一次失敗通常已把資料讀進快取，等一下再試一次多半就會成功
    for attempt in range(2):
        try:
            res = supabase.rpc("signal_performance", {"top_n": top_n, "horizon": horizon}).execute()
            return pd.DataFrame(res.data)
        except Exception:
            if attempt == 1:
                raise
            time.sleep(1)


# ===== 動能選股（momentum.py 產生）=====
def get_momentum_latest():
    """全市場最新一天的趨勢模板、52 週新高突破判斷"""
    return _latest_snapshot("momentum_signals", "date")

def get_momentum_events(since):
    """since 之後出現過的動能訊號（52 週新高＋量增、剛符合趨勢模板；只有 20 日均量夠的股票），依日期排序。
    momentum.py 只把有訊號的列多留一個月，給每日選股頁的事後追蹤用"""
    cols = "stock_id, date, close_price, trend_template, trend_new, breakout"
    frames = [pd.DataFrame(_fetch_all(lambda c=c: supabase.table("momentum_signals").select(cols)
                                      .gte("date", since).eq("liquid", True).eq(c, True)
                                      .order("date").order("stock_id")))
              for c in ("breakout", "trend_new")]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=[c.strip() for c in cols.split(",")])
    df = pd.concat(frames, ignore_index=True).drop_duplicates(["stock_id", "date"])
    return df.sort_values(["date", "stock_id"], ignore_index=True)

# ===== 資料更新狀態 =====
# (顯示名稱, 資料表, 日期欄位, 頻率)；有 stock_id 的表用權值股查最新日期，其餘直接排序（表都很小）
DATA_TABLES = [
    ("股價", "daily_prices", "date", "日"),
    ("三大法人買賣超", "stock_ins_daily", "date", "日"),
    ("融資融券", "margin_utilization", "date", "日"),
    ("大盤指數", "market_index", "date", "日"),
    ("期貨三大法人", "futures_institutional", "date", "日"),
    ("每日選股評分", "daily_signals", "date", "日"),
    ("大戶持股", "holder_distribution", "week_date", "週"),
    ("月營收", "monthly_revenue", "year_month", "月"),
    ("季報", "quarterly_report", None, "季"),
]
_NO_STOCK_ID = {"market_index", "futures_institutional"}

def _table_latest(table, date_col):
    if table == "quarterly_report":
        res = supabase.table(table).select("year, quarter") \
            .order("year", desc=True).order("quarter", desc=True).limit(1).execute()
        return f"{res.data[0]['year']}Q{res.data[0]['quarter']}" if res.data else None
    if table in _NO_STOCK_ID:
        res = supabase.table(table).select(date_col).order(date_col, desc=True).limit(1).execute()
        return res.data[0][date_col] if res.data else None
    return get_latest_date(table, date_col)

def get_data_status():
    """各資料表最新一筆資料的日期（月營收是年月、季報是 2026Q2 這種格式），依 DATA_TABLES 順序"""
    with ThreadPoolExecutor(max_workers=4) as pool:
        latest = list(pool.map(lambda t: _table_latest(t[1], t[2]), DATA_TABLES))
    return [{"name": name, "table": table, "freq": freq, "latest": d}
            for (name, table, _, freq), d in zip(DATA_TABLES, latest)]


# ===== 分組：每個登入帳號底下可以有多個分組，自選股、模擬持股都屬於某一個分組 =====
def get_groups(owner):
    """某帳號的分組（依建立順序）"""
    res = supabase.table("watch_groups").select("id, name").eq("owner", owner).order("id").execute()
    return pd.DataFrame(res.data, columns=["id", "name"])

def create_group(owner, name):
    supabase.table("watch_groups").insert({"owner": owner, "name": name}).execute()

def rename_group(group_id, name):
    supabase.table("watch_groups").update({"name": name}).eq("id", group_id).execute()

def delete_group(group_id):
    supabase.table("watch_groups").delete().eq("id", group_id).execute()

def claim_unowned_groups(owner):
    """分組功能上線前的資料放在 owner 為空字串的分組，交給主帳號"""
    supabase.table("watch_groups").update({"owner": owner}).eq("owner", "").execute()

def count_group_items(group_id):
    """分組裡的自選股與模擬持股筆數（刪除分組前檢查用）"""
    w = supabase.table("watchlist").select("stock_id", count="exact").eq("group_id", group_id).limit(1).execute()
    p = supabase.table("paper_trades").select("id", count="exact").eq("group_id", group_id).limit(1).execute()
    return (w.count or 0) + (p.count or 0)

# ===== 自選股 =====
def get_watchlist(group_id):
    """某分組的自選股清單（依加入時間由舊到新），不快取，新增或移除後馬上看得到"""
    res = supabase.table("watchlist").select("stock_id, note, added_at").eq("group_id", group_id) \
        .order("added_at", desc=False).execute()
    return pd.DataFrame(res.data, columns=["stock_id", "note", "added_at"])

def add_to_watchlist(group_id, stock_id, note=""):
    supabase.table("watchlist").upsert({"group_id": group_id, "stock_id": stock_id, "note": note}).execute()

def update_watchlist_note(group_id, stock_id, note):
    supabase.table("watchlist").update({"note": note}).eq("group_id", group_id).eq("stock_id", stock_id).execute()

def remove_from_watchlist(group_id, stock_id):
    supabase.table("watchlist").delete().eq("group_id", group_id).eq("stock_id", stock_id).execute()

# ===== 模擬持股（自己挑股票假設買賣）=====
def get_paper_trades(group_id):
    res = supabase.table("paper_trades").select("*").eq("group_id", group_id) \
        .order("buy_date", desc=False).order("id", desc=False).execute()
    return pd.DataFrame(res.data)

def add_paper_trades(group_id, rows):
    """rows 是多筆 {stock_id, source, buy_date, buy_price, shares, note}"""
    if rows:
        supabase.table("paper_trades").insert([{**r, "group_id": group_id} for r in rows]).execute()

def sell_paper_trade(trade_id, sell_date, sell_price):
    supabase.table("paper_trades").update({"sell_date": sell_date, "sell_price": sell_price}) \
        .eq("id", trade_id).execute()

def delete_paper_trade(trade_id):
    supabase.table("paper_trades").delete().eq("id", trade_id).execute()

def get_rows_for_stocks(table, stock_ids, date_col="date", since=None, columns="*"):
    """
    指定幾檔股票在 since 之後的資料（自選股頁用）。
    主鍵是 (stock_id, 日期)，用股票清單＋日期範圍查很快。
    """
    if not stock_ids:
        return pd.DataFrame()
    def query():
        q = supabase.table(table).select(columns).in_("stock_id", list(stock_ids))
        if since:
            q = q.gte(date_col, since)
        return q.order(date_col, desc=False).order("stock_id", desc=False)
    return pd.DataFrame(_fetch_all(query))
