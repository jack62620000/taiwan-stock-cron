"""
個股分析卡的資料與數據計算（個股分析卡頁與 LINE 推播共用，兩邊數字才會一致）
"""
import pandas as pd

from utils.db import (
    get_stock_info, get_daily_prices, get_quarterly, get_revenue, get_stock_ins,
    get_margin, get_holder, get_margin_utilization, get_market_index,
)
from utils.indicators import calc_all, get_latest_signals


def load_card_data(stock_id):
    info        = get_stock_info(stock_id)
    prices      = get_daily_prices(stock_id, limit=250)
    quarterly   = get_quarterly(stock_id, limit=8)
    revenue     = get_revenue(stock_id, limit=12)
    ins         = get_stock_ins(stock_id, limit=250)  # 買賣超圖用 60 日、外資持股走勢最多 250 日，卡片取最後 10 日
    margin      = get_margin(stock_id, limit=1)
    holder      = get_holder(stock_id, limit=1)
    utilization = get_margin_utilization(stock_id, limit=60)  # ✅ 新增
    # 上櫃股票和櫃買指數比，其餘和加權指數比
    index_code  = "TPEX" if (info or {}).get("market") == "上櫃" else "TAIEX"
    index_df    = get_market_index(index_code, limit=250)
    return info, prices, quarterly, revenue, ins, margin, holder, utilization, index_df


TREND_DAYS = 5  # 技術指標走向：和幾個交易日前比


def indicator_trend(series, flat, relative=False):
    """
    指標和 TREND_DAYS 個交易日前比，回傳 (箭頭, 變化量)，資料不夠回傳 None。
    變化小於 flat 算持平；relative=True 時 flat 是相對比例（MACD 柱狀的大小跟股價有關，只能看相對變化）。
    """
    s = pd.to_numeric(series, errors="coerce").dropna() if series is not None else pd.Series(dtype=float)
    if len(s) <= TREND_DAYS:
        return None
    now, before = float(s.iloc[-1]), float(s.iloc[-1 - TREND_DAYS])
    diff = now - before
    limit = flat * max(abs(now), abs(before)) if relative else flat
    arrow = "→" if abs(diff) <= limit else "↑" if diff > 0 else "↓"
    return arrow, diff


# 各指標走向的顯示：(小數位數, 前綴, 單位)；百分比類的變化是百分點
TREND_FORMAT = {
    "MACD": (2, "", ""),
    "乖離率（20 日）": (1, "", " 個百分點"),
    "布林通道": (0, "", " 個百分點"),
    "股價位階": (0, "", " 個百分點"),
    "均線排列": (2, "月線", " 元"),
    "量能": (1, "", " 倍"),
}


def trend_text(t, key=None):
    """(箭頭, 變化量) → 「5 日往上 ↑ +6.2」；None → 空字串"""
    if not t:
        return ""
    arrow, diff = t
    decimals, prefix, unit = TREND_FORMAT.get(key, (1, "", ""))
    word = {"↑": "往上", "↓": "往下", "→": "持平"}[arrow]
    return f"{prefix}{TREND_DAYS} 日{word} {arrow} {diff:+.{decimals}f}{unit}"


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
    latest_holder = holder.iloc[-1] if not holder.empty else {}
    latest_util   = utilization.iloc[-1] if not utilization.empty else {}  # ✅ 新增

    ins_10d      = ins.tail(10)
    foreign_10d  = ins_10d["foreign_net_shares"].sum() if not ins.empty else 0
    sitca_10d    = ins_10d["sitca_net_shares"].sum() if not ins.empty else 0
    dealers_10d  = ins_10d["dealers_net_shares"].sum() if not ins.empty else 0

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
    macd_hist  = signals.get("MACD", {}).get("hist")

    # ===== 其他技術面燈號：均線排列、乖離率、布林通道、量能 =====
    # 燈號顏色沿用上面：偏多（紅）、過熱或偏空（綠）、中性（金）
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
            extra_tech.append(("均線排列", "多頭排列", "bull", "5 日 > 20 日 > 60 日"))
        elif ma5 < ma20 < ma60:
            extra_tech.append(("均線排列", "空頭排列", "bear", "5 日 < 20 日 < 60 日"))
        else:
            extra_tech.append(("均線排列", "均線糾結", "neu", "5、20、60 日線交錯"))
    if ma20:
        bias = (close / ma20 - 1) * 100
        extra_tech.append(("乖離率（20 日）", f"{bias:+.1f}%",
                           "bear" if bias > 10 else "bull" if bias < -10 else "neu",
                           "正乖離過大，短線過熱" if bias > 10 else "負乖離過大，可能超跌" if bias < -10 else
                           "站上 20 日線" if bias >= 0 else "跌破 20 日線"))
    if bb_up is not None and bb_low is not None and bb_up > bb_low:
        bb_pos = (close - bb_low) / (bb_up - bb_low) * 100
        extra_tech.append(("布林通道", f"{bb_pos:.0f}%",
                           "bear" if bb_pos >= 80 else "bull" if bb_pos <= 20 else "neu",
                           "突破上軌" if bb_pos > 100 else "靠近上軌" if bb_pos >= 80 else
                           "跌破下軌" if bb_pos < 0 else "靠近下軌" if bb_pos <= 20 else "通道中段"))
    if vol_ratio is not None:
        if vol_ratio >= 1.5:
            extra_tech.append(("量能", f"{vol_ratio:.1f} 倍", "bull" if change >= 0 else "bear",
                               "價漲量增" if change >= 0 else "價跌量增"))
        else:
            extra_tech.append(("量能", f"{vol_ratio:.1f} 倍", "neu", "量縮" if vol_ratio < 0.7 else "量能正常"))

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
        "macd_hist": macd_hist,
        "ma5": ma5,
        "ma20": ma20,
        "ma60": ma60,
        "vol_ratio": vol_ratio,
        "trends": trends,
        "crosses": crosses,
        "extra_tech": extra_tech,
        "hold_ratio": hold_ratio,
    }
