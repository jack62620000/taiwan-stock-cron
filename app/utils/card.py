"""
個股分析卡的資料與數據計算（個股分析卡頁與 LINE 推播共用，兩邊數字才會一致）
"""
import pandas as pd

from utils.db import (
    get_stock_info, get_daily_prices, get_quarterly, get_revenue, get_stock_ins,
    get_margin, get_holder, get_margin_utilization, get_market_index,
)
from utils.indicators import (calc_all, get_latest_signals, level, volume_level, obv_state, macd_zero, LAMP,
                              POSITION_LEVELS, BOLL_LEVELS, BIAS_LEVELS, INF)


def load_card_data(stock_id):
    info        = get_stock_info(stock_id)
    prices      = get_daily_prices(stock_id, limit=250)
    quarterly   = get_quarterly(stock_id, limit=8)
    revenue     = get_revenue(stock_id, limit=12)
    ins         = get_stock_ins(stock_id, limit=250)  # 買賣超圖用 60 日、外資持股走勢最多 250 日，卡片取最後 10 日
    margin      = get_margin(stock_id, limit=1)
    holder      = get_holder(stock_id, limit=5)  # 每週一筆，取 5 週算近 4 週變化（LINE 整批卡片 HOLDER_WEEKS 要一樣）
    utilization = get_margin_utilization(stock_id, limit=21)  # 最新一筆＋近 20 日增減（LINE 整批卡片 MARGIN_DAYS 要一樣）
    # 上櫃股票和櫃買指數比，其餘和加權指數比
    index_code  = "TPEX" if (info or {}).get("market") == "上櫃" else "TAIEX"
    index_df    = get_market_index(index_code, limit=250)
    return info, prices, quarterly, revenue, ins, margin, holder, utilization, index_df


TREND_DAYS = 5  # 技術指標走向：和幾個交易日前比


def indicator_trend(series, flat, relative=False):
    """
    指標和 TREND_DAYS 個交易日前比，回傳 (箭頭, 變化量, 當時的值, 現在的值)，資料不夠回傳 None。
    變化小於 flat 算持平；relative=True 時 flat 是相對比例（MACD 柱狀的大小跟股價有關，只能看相對變化）。
    """
    s = pd.to_numeric(series, errors="coerce").dropna() if series is not None else pd.Series(dtype=float)
    if len(s) <= TREND_DAYS:
        return None
    now, before = float(s.iloc[-1]), float(s.iloc[-1 - TREND_DAYS])
    diff = now - before
    limit = flat * max(abs(now), abs(before)) if relative else flat
    arrow = "→" if abs(diff) <= limit else "↑" if diff > 0 else "↓"
    return arrow, diff, before, now


# 各指標走向的顯示：(小數位數, 前綴, 變化量單位, 數值單位)；百分比類的變化是百分點
TREND_FORMAT = {
    "MACD": (2, "", "", ""),
    "乖離率（20 日）": (1, "", " 個百分點", "%"),
    "布林通道": (0, "", " 個百分點", "%"),
    "股價位階": (0, "", " 個百分點", "%"),
    "均線排列": (2, "月線", " 元", ""),
    "量能": (1, "", " 倍", ""),
}


def trend_text(t, key=None):
    """
    (箭頭, 變化量, 當時, 現在) → 「↓ 比 5 日前少 15.8（60.2 → 44.4）」；None → 空字串。
    變化量是「現在的值」減「5 個交易日前的值」，不是這 5 天股價的漲跌。
    """
    if not t:
        return ""
    arrow, diff = t[0], t[1]
    decimals, prefix, unit, vunit = TREND_FORMAT.get(key, (1, "", "", ""))
    span = f"（{t[2]:.{decimals}f}{vunit} → {t[3]:.{decimals}f}{vunit}）" if len(t) >= 4 else ""
    if arrow == "→":
        return f"→ {prefix}和 {TREND_DAYS} 日前差不多{span}"
    word = "多" if diff > 0 else "少"
    return f"{arrow} {prefix}比 {TREND_DAYS} 日前{word} {abs(diff):.{decimals}f}{unit}{span}"


def _streak(s):
    """序列最後連續幾天同號（正或負），回傳 (天數, 1 或 -1)；最新一筆是 0 或沒資料回傳 (0, 0)"""
    s = pd.to_numeric(s, errors="coerce").dropna()
    if s.empty or s.iloc[-1] == 0:
        return 0, 0
    sign = 1 if s.iloc[-1] > 0 else -1
    same = (s * sign > 0)[::-1]
    return int(same.cumprod().sum()), sign


def chip_trend(net_shares):
    """
    法人每日買賣超（股）→ 近 10 日累計、近 5 日與前 5 日（張），以及「轉為買超」「買超加速」等趨勢說明。
    回傳 dict：sum10、last5、prev5、label、arrow（↑ 偏買、↓ 偏賣、→ 不明顯）、streak（例如「連買 3 天」）
    """
    s = pd.to_numeric(net_shares, errors="coerce").fillna(0) / 1000
    last5, prev5 = float(s.tail(5).sum()), float(s.iloc[-10:-5].sum()) if len(s) > 5 else 0.0
    if last5 > 0 and prev5 <= 0:
        label, arrow = "由賣轉買", "↑"
    elif last5 < 0 and prev5 >= 0:
        label, arrow = "由買轉賣", "↓"
    elif last5 > 0:
        label, arrow = ("買超加速", "↑") if last5 > prev5 else ("買超減緩", "→")
    elif last5 < 0:
        label, arrow = ("賣超加速", "↓") if last5 < prev5 else ("賣超減緩", "→")
    else:
        label, arrow = "沒有進出", "→"
    days, sign = _streak(s)
    streak = f"連{'買' if sign > 0 else '賣'} {days} 天" if days >= 2 else ""
    return {"sum10": float(s.tail(10).sum()), "last5": last5, "prev5": prev5,
            "label": label, "arrow": arrow, "streak": streak, "streak_days": days * (1 if sign > 0 else -1)}


# ===== 回測後的燈號 =====
# 回測方式：訊號隔天開盤買、持有 20 個交易日，和同期全部股票平均比；前後兩段時間都一致才給紅綠燈。
# 燈號：bull 紅＝回測比平均好、bear 綠＝回測比平均差、neu 金＝差別不大或前後不一致
YOY_LEVELS = [  # 單月營收年增率（%）
    (20, "高成長", "bull", "年增 20% 以上，回測之後 20 日比平均好"),
    (0, "小幅成長", "neu", "年增 0～20%，回測和平均差不多"),
    (-20, "衰退", "bear", "年減，回測之後 20 日比平均差"),
    (INF, "大幅衰退", "bear", "年減超過 20%，回測之後 20 日明顯比平均差"),
]
YOY3_LEVELS = [  # 近 3 月年增率（%）
    (10, "成長", "bull", "近 3 月年增 10% 以上，回測比平均好"),
    (0, "小幅成長", "neu", "近 3 月年增 0～10%，回測和平均差不多"),
    (INF, "衰退", "bear", "近 3 月年減，回測比平均差"),
]
CUM_YOY_LEVELS = [  # 今年累計營收年增率（%）
    (20, "高成長", "bull", "累計年增 20% 以上，回測比平均好"),
    (0, "持平", "neu", "累計年增 0～20%，回測和平均一樣"),
    (INF, "衰退", "bear", "累計年減，回測比平均差"),
]
SHORT_RATIO_LEVELS = [  # 券資比（%）
    (20, "很高", "bull", "券資比 20% 以上，回測比平均好，但後段時間差距縮小、波動大"),
    (5, "偏高", "bull", "券資比 5～20%，回測明顯比平均好（空單回補的買盤）"),
    (INF, "一般", "neu", "券資比 5% 以下，回測和平均差不多"),
]


def chip_lamp(name, c):
    """法人趨勢的回測燈號 → (燈號, 說明)。外資看買或賣、投信只看連買、自營商影響很小"""
    if not c:
        return "neu", ""
    days = c.get("streak_days", 0)
    if name == "投信":
        if days >= 3:
            return "bull", "投信連買 3 天以上，回測明顯比平均好"
        return "neu", "投信的買賣方向回測差別不大（連買才明顯）"
    if name == "外資":
        if c["label"] in ("由賣轉買", "由買轉賣"):
            return "neu", "剛轉向，回測和平均差不多"
        if c["last5"] > 0:
            return "bull", "近 5 日買超，回測略比平均好"
        if c["last5"] < 0:
            return "bear", "近 5 日賣超，回測比平均差"
        return "neu", ""
    return "neu", "自營商進出多為避險，回測影響很小"


def big_holder_lamp(big_chg):
    """大戶持股近 4 週變化（百分點）的回測燈號 → (燈號, 說明)"""
    if big_chg is None:
        return "neu", ""
    if big_chg >= 1:
        return "bull", "大戶明顯加碼（增加 1 個百分點以上），回測比平均好"
    return "neu", "變化不到 1 個百分點時，增減回測差別不大"


def revenue_lamps(latest_yoy, rv):
    """營收各項的回測燈號 → dict：yoy、yoy3、cum 是 (名稱, 燈號, 說明)；rank、streak 是 (燈號, 說明)"""
    num = lambda v: float(pd.to_numeric(v, errors="coerce")) if v is not None else float("nan")
    rank = ("bull", "創近 12 個月新高，回測明顯比平均好") if rv["rank12"] == 1 else ("neu", "")
    days = rv.get("yoy_streak_days", 0)
    if days >= 3:
        streak = ("bull", "連續年增 3 個月以上，回測比平均好")
    elif days < 0:
        streak = ("bear", "最新月年減，回測比平均差")
    else:
        streak = ("neu", "")
    return {"yoy": level(YOY_LEVELS, num(latest_yoy)), "yoy3": level(YOY3_LEVELS, num(rv["yoy3"])),
            "cum": level(CUM_YOY_LEVELS, num(rv["cum_yoy"])), "rank": rank, "streak": streak}


def balance_trend(balance):
    """
    融資或融券餘額（張）→ 近 5 日、近 20 日增減（張與 %）和連續增減天數。資料不夠回傳 None。
    """
    s = pd.to_numeric(balance, errors="coerce").dropna()
    if len(s) < 2:
        return None
    now = float(s.iloc[-1])

    def chg(n):
        if len(s) <= n:
            return None, None
        before = float(s.iloc[-1 - n])
        return now - before, ((now / before - 1) * 100 if before else None)

    c5, p5 = chg(5)
    c20, p20 = chg(20)
    days, sign = _streak(s.diff())
    return {"chg5": c5, "pct5": p5, "chg20": c20, "pct20": p20,
            "streak": f"連{'增' if sign > 0 else '減'} {days} 天" if days >= 2 else ""}


def margin_hint(margin_chg20, price_chg20):
    """融資 20 日增減搭配股價 20 日漲跌的解讀，附上回測結果（隔天開盤買、持有 20 日和全部股票平均比）"""
    if margin_chg20 is None or price_chg20 is None or margin_chg20 == 0:
        return ""
    if margin_chg20 > 0:
        return "股價漲、融資也增加：追價的人變多，回測之後 20 日反而比平均好" if price_chg20 >= 0 else \
               "股價跌、融資卻增加：散戶逢低承接，籌碼比較亂，回測之後 20 日比平均差"
    return "股價漲、融資減少：籌碼比較安定，回測和平均差不多" if price_chg20 >= 0 else \
           "股價跌、融資也減少：散戶在退場（或被斷頭），回測之後 20 日比平均差"


def revenue_trend(revenue):
    """
    月營收（千元，由舊到新）→ 營收表現的延伸數據：累計營收與年增率、近 3 月年增率（和前 3 月比）、
    在近 12 個月排第幾高、年增連續幾個月、近 12 個月營收與年增率（畫小長條圖用）。資料不夠的欄位是 None。
    """
    out = {"cum_rev": None, "cum_yoy": None, "yoy3": None, "yoy3_prev": None, "rank12": None,
           "n12": 0, "yoy_streak": "", "yoy_streak_days": 0, "bars": []}
    if revenue is None or revenue.empty:
        return out
    r = revenue.sort_values("year_month")
    num = lambda c: pd.to_numeric(r[c], errors="coerce") if c in r.columns else pd.Series([None] * len(r), dtype=float)
    rev, ly, yoy = num("revenue"), num("last_year_revenue"), num("yoy_growth")
    ly = ly.fillna(rev / (1 + yoy / 100))  # 沒有去年同月營收時，用年增率反推
    last = r.iloc[-1]
    out["cum_rev"] = pd.to_numeric(last.get("cumulative_revenue"), errors="coerce")
    out["cum_yoy"] = pd.to_numeric(last.get("cumulative_yoy_growth"), errors="coerce")

    def yoy_of(a, b):
        cur, base = rev.iloc[a:b], ly.iloc[a:b]
        if len(cur) == 3 and cur.notna().all() and base.notna().all() and base.sum() > 0:
            return (cur.sum() / base.sum() - 1) * 100
        return None

    out["yoy3"] = yoy_of(-3, None)
    out["yoy3_prev"] = yoy_of(-6, -3) if len(r) >= 6 else None
    last12 = rev.tail(12).dropna()
    if not last12.empty and pd.notna(rev.iloc[-1]):
        out["rank12"] = int((last12 > rev.iloc[-1]).sum()) + 1
        out["n12"] = len(last12)
    days, sign = _streak(yoy)
    if days >= 2:
        out["yoy_streak"] = f"連續 {days} 個月年{'增' if sign > 0 else '減'}"
    out["yoy_streak_days"] = days * (1 if sign > 0 else -1)
    out["bars"] = [(str(ym), v, g) for ym, v, g in zip(r["year_month"].tail(12), rev.tail(12), yoy.tail(12))]
    return out


def cross_state(diff, fast, slow):
    """
    快線減慢線（diff）的最新狀態：快線在上面是往上、在下面是往下，並算出幾天前交叉。
    回傳 (箭頭, 說明)；資料不夠回傳 None。例如 ("↑", "K 在 D 上方，往上（3 天前黃金交叉）")
    """
    s = pd.to_numeric(diff, errors="coerce").dropna() if diff is not None else pd.Series(dtype=float)
    if len(s) < 2 or s.iloc[-1] == 0:
        return None
    up = s.iloc[-1] > 0
    side = (s > 0) if up else (s < 0)
    days = int(side[::-1].cumprod().sum())  # 連續幾天在同一邊
    cross = "黃金交叉" if up else "死亡交叉"
    when = "今天" if days == 1 else f"{days - 1} 天前"
    note = f"（{when}{cross}）" if days < len(s) else ""
    return ("↑" if up else "↓"), f"{fast} 在 {slow} {'上方，往上' if up else '下方，往下'}{note}"


def card_metrics(prices, quarterly, revenue, ins, margin, holder, utilization):
    """
    分析卡上的各項數據：52 週區間、基本面（EPS、本益比、ROE、P/B、現金流）、營收、
    籌碼（近 10 日法人、外資持股）、融資融券、技術面燈號。prices 不可為空。回傳 dict。
    """
    prices_ta = calc_all(prices.copy())
    signals   = get_latest_signals(prices_ta)

    # 停牌或沒有成交的日子已在 calc_all 補成股價不變，並以 no_trade 標記
    latest    = prices_ta.iloc[-1]
    prev      = prices_ta.iloc[-2] if len(prices_ta) > 1 else latest
    no_trade_days = prices_ta[prices_ta["no_trade"]]["date"].astype(str).tolist()
    close     = latest["close_price"]
    prev_close = prev["close_price"]
    change    = close - prev_close
    change_pct = (change / prev_close * 100) if prev_close else 0

    high_52w  = prices["high_price"].max()
    low_52w   = prices["low_price"].min()
    position_pct = ((close - low_52w) / (high_52w - low_52w) * 100) if high_52w != low_52w else 50

    latest_q      = quarterly.iloc[-1] if not quarterly.empty else {}
    latest_rev    = revenue.iloc[-1] if not revenue.empty else {}
    latest_margin = margin.iloc[-1] if not margin.empty else {}
    latest_holder = holder.sort_values("week_date").iloc[-1] if not holder.empty else {}
    latest_util   = utilization.iloc[-1] if not utilization.empty else {}  # ✅ 新增

    ins_10d      = ins.tail(10)
    foreign_10d  = ins_10d["foreign_net_shares"].sum() if not ins.empty else 0
    sitca_10d    = ins_10d["sitca_net_shares"].sum() if not ins.empty else 0
    dealers_10d  = ins_10d["dealers_net_shares"].sum() if not ins.empty else 0

    # 法人買賣趨勢：近 5 日和前 5 日比，看是不是開始買或開始賣
    chips = {name: chip_trend(ins[col]) if col in ins.columns else None
             for name, col in (("外資", "foreign_net_shares"), ("投信", "sitca_net_shares"), ("自營商", "dealers_net_shares"))}

    # 大戶持股比例：最新值與近 4 週變化（百分點）
    big = pd.to_numeric(holder["big_holder_ratio"], errors="coerce").dropna() \
        if "big_holder_ratio" in holder.columns else pd.Series(dtype=float)
    big_chg = float(big.iloc[-1] - big.iloc[0]) if len(big) > 1 else None
    big_weeks = len(big) - 1

    # 融資融券：餘額增減與連續天數、券資比、融資增減搭配股價的解讀
    margin_tr = balance_trend(utilization["margin_balance"]) if "margin_balance" in utilization.columns else None
    short_tr = balance_trend(utilization["short_balance"]) if "short_balance" in utilization.columns else None
    mb = pd.to_numeric(latest_util.get("margin_balance") if len(latest_util) else None, errors="coerce")
    sb = pd.to_numeric(latest_util.get("short_balance") if len(latest_util) else None, errors="coerce")
    short_margin_ratio = float(sb / mb * 100) if pd.notna(mb) and pd.notna(sb) and mb > 0 else None
    closes = pd.to_numeric(prices_ta["close_price"], errors="coerce")
    price_chg20 = float(closes.iloc[-1] / closes.iloc[-21] - 1) * 100 if len(closes) > 20 else None
    hint = margin_hint(margin_tr["chg20"] if margin_tr else None, price_chg20)

    rev_tr = revenue_trend(revenue)
    rev_lamps = revenue_lamps(latest_rev.get("yoy_growth") if len(latest_rev) else None, rev_tr)
    chip_lamps = {name: chip_lamp(name, c) for name, c in chips.items()}
    big_lamp = big_holder_lamp(big_chg)
    short_level = level(SHORT_RATIO_LEVELS, short_margin_ratio if short_margin_ratio is not None else float("nan"))

    # 外資持股比率：最新值與近 20 個交易日的變化（百分點）
    hold_ratio = pd.to_numeric(ins["foreign_hold_ratio"], errors="coerce").dropna() \
        if "foreign_hold_ratio" in ins.columns else pd.Series(dtype=float)
    hold_latest = hold_ratio.iloc[-1] if not hold_ratio.empty else None
    hold_chg_20 = hold_latest - hold_ratio.iloc[-21] if len(hold_ratio) > 20 else None

    # 現金流量（千元，年初累計）→ 億元
    def to_yi(v):
        return v / 1e5 if v is not None and pd.notna(v) else None

    op_cf   = to_yi(latest_q.get("operating_cf"))
    inv_cf  = to_yi(latest_q.get("investing_cf"))
    free_cf = to_yi(latest_q.get("free_cf"))

    # ===== 計算本益比 =====
    # 季報的 EPS 是「年初至該季」的累計值，不是單季數字：
    #   - 第 4 季的累計就是全年 EPS
    #   - 有去年資料時算近四季：本期累計 + 去年全年 − 去年同期累計
    #   - 否則用累計值年化：EPS × 4 ÷ 季別
    pe_ratio = None
    pe_note  = ""
    q_label  = ""
    if not quarterly.empty:
        q_sorted = quarterly.sort_values(["year", "quarter"])
        last_q   = q_sorted.iloc[-1]
        q_year   = int(last_q["year"])
        q_num    = int(last_q["quarter"])
        q_label  = f"{q_year} 年前 {q_num} 季累計"
        eps_now  = last_q.get("eps")

        annual_eps = None
        if eps_now is not None and pd.notna(eps_now) and q_num > 0:
            prev_full = q_sorted[(q_sorted["year"] == q_year - 1) & (q_sorted["quarter"] == 4)]["eps"].dropna()
            prev_same = q_sorted[(q_sorted["year"] == q_year - 1) & (q_sorted["quarter"] == q_num)]["eps"].dropna()
            if q_num == 4:
                annual_eps = eps_now
                pe_note    = "全年"
            elif not prev_full.empty and not prev_same.empty:
                annual_eps = eps_now + prev_full.iloc[-1] - prev_same.iloc[-1]
                pe_note    = "近4季"
            else:
                annual_eps = eps_now * 4 / q_num
                pe_note    = f"前{q_num}季年化"
        if annual_eps and annual_eps > 0 and close:
            pe_ratio = round(close / annual_eps, 2)

    # ===== P/B 用最新收盤價現算（資料庫存的是抓季報當下的股價）=====
    book_value = latest_q.get("book_value")
    if book_value is not None and pd.notna(book_value) and book_value > 0 and close:
        pb_now = round(close / book_value, 2)
    else:
        pb_now = latest_q.get("pb_ratio")


    rsi_val    = signals.get("RSI", {}).get("value")
    rsi_status = signals.get("RSI", {}).get("status", "N/A")
    rsi_note   = signals.get("RSI", {}).get("note", "")
    kd_k       = signals.get("KD", {}).get("K")
    kd_d       = signals.get("KD", {}).get("D")
    kd_status  = signals.get("KD", {}).get("status", "N/A")
    kd_note    = signals.get("KD", {}).get("note", "")
    position_level = level(POSITION_LEVELS, position_pct)  # (名稱, 燈號, 說明)
    macd_hist  = signals.get("MACD", {}).get("hist")
    macd_zero_state = macd_zero(signals.get("MACD", {}).get("value"), signals.get("MACD", {}).get("signal"))

    # ===== 其他技術面燈號：均線排列、乖離率、布林通道、量能 =====
    # 每筆是 (指標, 數值, 燈號, 分級名稱, 說明)；燈號偏多紅、過熱或偏弱綠、中性金，分級見 indicators.py 的回測
    def _num(v):
        v = pd.to_numeric(v, errors="coerce")
        return None if pd.isna(v) else float(v)

    ma5, ma20, ma60 = (_num(latest.get(c)) for c in ("MA5", "MA20", "MA60"))
    bb_up, bb_low = _num(latest.get("BB_UP")), _num(latest.get("BB_LOW"))
    vol_s = pd.to_numeric(prices_ta["volume"], errors="coerce")
    vol_avg20 = vol_s.iloc[-21:-1].mean() if len(vol_s) > 20 else None
    vol_ratio = float(vol_s.iloc[-1] / vol_avg20) if vol_avg20 else None

    # ===== 技術指標近 5 個交易日的走向（↑ 往上、↓ 往下、→ 持平）=====
    def _series(col):
        return pd.to_numeric(prices_ta[col], errors="coerce") if col in prices_ta.columns else pd.Series(dtype=float)

    ma20_s = _series("MA20")
    bb_width = _series("BB_UP") - _series("BB_LOW")
    close_s = _series("close_price")
    crosses = {
        "KD": cross_state(_series("K") - _series("D"), "K", "D"),
        "MACD": cross_state(_series("MACD_HIST"), "MACD 線", "訊號線"),
    }
    # 回測：K 在 80 以上死亡交叉之後反而比一般股票強，20 以下黃金交叉之後也沒有比較好
    k_now = _num(latest.get("K"))
    if crosses["KD"] and k_now is not None:
        arrow, text = crosses["KD"]
        if arrow == "↓" and k_now >= 80 and "死亡交叉" in text:
            crosses["KD"] = arrow, text + "；高檔死亡交叉在回測中不代表要跌"
        elif arrow == "↑" and k_now < 20 and "黃金交叉" in text:
            crosses["KD"] = arrow, text + "；低檔黃金交叉在回測中不代表會漲"
    trends = {
        "RSI": indicator_trend(_series("RSI"), 2),
        "KD": indicator_trend(_series("K"), 3),
        "MACD": indicator_trend(_series("MACD_HIST"), 0.05, relative=True),
        "乖離率（20 日）": indicator_trend((close_s / ma20_s - 1) * 100, 1),
        "布林通道": indicator_trend((close_s - _series("BB_LOW")) / bb_width.where(bb_width > 0) * 100, 10),
        "均線排列": indicator_trend(ma20_s, 0.005, relative=True),  # 看 20 日線（月線）本身往上還往下
        "量能": indicator_trend(vol_s / vol_s.shift(1).rolling(20).mean(), 0.3),
        "股價位階": indicator_trend((close_s - low_52w) / (high_52w - low_52w) * 100, 3)
        if high_52w != low_52w else None,
    }

    extra_tech = []
    if None not in (ma5, ma20, ma60):
        if ma5 > ma20 > ma60:
            extra_tech.append(("均線排列", "多頭排列", "bull", "多頭排列", "5 日 > 20 日 > 60 日"))
        elif ma5 < ma20 < ma60:
            extra_tech.append(("均線排列", "空頭排列", "bear", "空頭排列", "5 日 < 20 日 < 60 日"))
        else:
            extra_tech.append(("均線排列", "均線糾結", "neu", "均線糾結", "5、20、60 日線交錯"))
    if ma20:
        bias = (close / ma20 - 1) * 100
        name, lamp, note = level(BIAS_LEVELS, bias)
        extra_tech.append(("乖離率（20 日）", f"{bias:+.1f}%", lamp, name, note))
    if bb_up is not None and bb_low is not None and bb_up > bb_low:
        bb_pos = (close - bb_low) / (bb_up - bb_low) * 100
        name, lamp, note = level(BOLL_LEVELS, bb_pos)
        extra_tech.append(("布林通道", f"{bb_pos:.0f}%", lamp, name, note))
    if vol_ratio is not None:
        name, lamp, note = volume_level(vol_ratio, change)
        extra_tech.append(("量能", f"{vol_ratio:.1f} 倍", lamp, name, note))
    obv = obv_state(close_s, _series("OBV"))
    if obv:
        extra_tech.append(("OBV 能量潮", obv[0], obv[1], obv[0], obv[2]))

    # ===== 技術面總結：數 ⑥ 每一格的燈號（只是統計，沒有另外回測）=====
    lamps = [LAMP.get(rsi_status, "neu"), LAMP.get(kd_status, "neu"),
             "bull" if (macd_hist or 0) > 0 else "bear", position_level[1]] + [cls for _, _, cls, _, _ in extra_tech]
    red, green = lamps.count("bull"), lamps.count("bear")
    gold = len(lamps) - red - green
    verdict = "整體偏多" if red - green >= 2 else "整體偏弱" if green - red >= 2 else "多空交錯"
    tech_summary = {"red": red, "green": green, "gold": gold, "verdict": verdict,
                    "lamp": "bull" if red - green >= 2 else "bear" if green - red >= 2 else "neu"}

    return {
        "prices_ta": prices_ta,
        "signals": signals,
        "latest": latest,
        "prev": prev,
        "no_trade_days": no_trade_days,
        "close": close,
        "prev_close": prev_close,
        "change": change,
        "change_pct": change_pct,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "position_pct": position_pct,
        "latest_q": latest_q,
        "latest_rev": latest_rev,
        "latest_margin": latest_margin,
        "latest_holder": latest_holder,
        "latest_util": latest_util,
        "foreign_10d": foreign_10d,
        "sitca_10d": sitca_10d,
        "dealers_10d": dealers_10d,
        "hold_latest": hold_latest,
        "hold_chg_20": hold_chg_20,
        "op_cf": op_cf,
        "inv_cf": inv_cf,
        "free_cf": free_cf,
        "pe_ratio": pe_ratio,
        "pe_note": pe_note,
        "q_label": q_label,
        "book_value": book_value,
        "pb_now": pb_now,
        "rsi_val": rsi_val,
        "rsi_status": rsi_status,
        "rsi_note": rsi_note,
        "kd_k": kd_k,
        "kd_d": kd_d,
        "kd_status": kd_status,
        "kd_note": kd_note,
        "position_level": position_level,
        "macd_hist": macd_hist,
        "macd_zero": macd_zero_state,
        "tech_summary": tech_summary,
        "ma5": ma5,
        "ma20": ma20,
        "ma60": ma60,
        "vol_ratio": vol_ratio,
        "trends": trends,
        "crosses": crosses,
        "extra_tech": extra_tech,
        "hold_ratio": hold_ratio,
        "chips": chips,
        "big_chg": big_chg,
        "big_weeks": big_weeks,
        "margin_tr": margin_tr,
        "short_tr": short_tr,
        "short_margin_ratio": short_margin_ratio,
        "price_chg20": price_chg20,
        "margin_hint": hint,
        "rev_tr": rev_tr,
        "rev_lamps": rev_lamps,
        "chip_lamps": chip_lamps,
        "big_lamp": big_lamp,
        "short_level": short_level,
    }
