"""
大盤趨勢分析（首頁和全台股總覽的大盤區塊共用）

analyze 只用資料算出結論和各項說明，不碰 Streamlit，方便測試；render 把結果畫成一個面板。
看四件事：加權指數的均線位置與方向、量能、外資現股買賣超金額、外資台指期淨未平倉的變化，
最後列出上下最近的均線當支撐、壓力，以及「明日買進」的大盤濾網（月線和季線之上）是不是綠燈。
"""
import pandas as pd
import streamlit as st

from utils.ui import BORDER, DOWN_COLOR, MUTED, PANEL, TEXT, UP_COLOR

MA_NAMES = {5: "5 日線", 20: "月線", 60: "季線", 120: "半年線", 240: "年線"}
HISTORY_DAYS = 60  # 趨勢色帶看近幾個交易日
INDEX_DAYS = 320   # 年線要 240 天，加上算均線方向和色帶最前面那天要的天數


def foreign_amount(ins, closes):
    """全市場外資每天買賣超金額（億元）＝ Σ 各股買賣超股數 × 當天收盤價；回傳以日期為索引的 Series"""
    if ins is None or ins.empty or closes is None or closes.empty:
        return pd.Series(dtype=float)
    df = ins[["stock_id", "date", "foreign_net_shares"]].merge(
        closes[["stock_id", "date", "close_price"]], on=["stock_id", "date"], how="inner")
    amt = pd.to_numeric(df["foreign_net_shares"], errors="coerce") * pd.to_numeric(df["close_price"], errors="coerce")
    out = amt.groupby(df["date"].astype(str)).sum() / 1e8
    return out.sort_index()


def _slope(ma, days=5):
    """均線方向：和 days 天前比，> 0.1% 上揚、< -0.1% 下彎，其他走平"""
    if len(ma.dropna()) <= days:
        return "走平"
    now, before = ma.iloc[-1], ma.iloc[-1 - days]
    chg = (now / before - 1) * 100
    return "上揚" if chg > 0.1 else "下彎" if chg < -0.1 else "走平"


def _streak(values):
    """最後連續同方向（買超或賣超）幾天：回傳 (方向 1/-1/0, 天數)"""
    sign, n = 0, 0
    for v in reversed(list(values)):
        s = 1 if v > 0 else -1 if v < 0 else 0
        if n == 0:
            sign, n = s, 1
        elif s == sign and s != 0:
            n += 1
        else:
            break
    return sign, n


def _verdict(close):
    """依收盤價（由舊到新）判斷最後一天的趨勢：回傳 (結論, tone)。長線看年線，中短線看月線、季線"""
    c = close.iloc[-1]
    ma = {n: close.rolling(n).mean() for n in (20, 60, 240)}
    last = {n: s.iloc[-1] for n, s in ma.items() if pd.notna(s.iloc[-1])}
    slope = {n: _slope(ma[n]) for n in last}
    long_up = 240 in last and c > last[240] and slope.get(240) == "上揚"
    up_short = 20 in last and 60 in last and c > last[20] > last[60]
    down_short = 20 in last and 60 in last and c < last[20] < last[60]
    if up_short and slope.get(60) == "上揚":
        return "多頭", 1
    if down_short and slope.get(60) == "下彎":
        return "空頭", -1
    if long_up and 20 in last and c < last[20]:
        return "長多格局中的回檔", 0
    if 60 in last and c > last.get(20, c) and slope.get(60) == "下彎":
        return "空頭中的反彈", 0
    return "盤整", 0


def verdict_history(index_df, days=HISTORY_DAYS):
    """近 days 個交易日每天的趨勢結論：[(日期, 結論, tone)]，由舊到新；每天只用到當天為止的資料"""
    if index_df is None or len(index_df) < 61:
        return []
    df = index_df.sort_values("date")
    close = pd.to_numeric(df["close"], errors="coerce").reset_index(drop=True)
    dates = df["date"].astype(str).str[:10].tolist()
    start = max(60, len(close) - days)
    return [(dates[i], *_verdict(close.iloc[:i + 1])) for i in range(start, len(close))]


def last_change(history):
    """最近一次結論改變：(日期, 之前的結論, 之後的結論)；色帶期間都沒變回傳 None"""
    for k in range(len(history) - 1, 0, -1):
        if history[k][1] != history[k - 1][1]:
            return history[k][0], history[k - 1][1], history[k][1]
    return None


def analyze(index_df, foreign=None, futures=None):
    """
    index_df：加權指數（get_market_index，由舊到新）；foreign：外資每天買賣超金額（億，foreign_amount）；
    futures：臺股期貨三大法人（get_futures_ins）。
    回傳 {"date", "verdict", "tone"（1 偏多／-1 偏空／0 中性）, "summary", "items": [(項目, 說明, tone)]}，資料不夠時回傳 None。
    """
    if index_df is None or len(index_df) < 61:
        return None
    df = index_df.sort_values("date")
    close = pd.to_numeric(df["close"], errors="coerce").reset_index(drop=True)
    value = pd.to_numeric(df["trade_value"], errors="coerce").reset_index(drop=True)
    high = pd.to_numeric(df["high"], errors="coerce").fillna(close).reset_index(drop=True)
    c = close.iloc[-1]
    ma = {n: close.rolling(n).mean() for n in MA_NAMES}
    last = {n: s.iloc[-1] for n, s in ma.items() if pd.notna(s.iloc[-1])}
    slope = {n: _slope(ma[n]) for n in last}
    items = []

    # 1. 均線：在哪幾條之上、之下，月線和季線的方向
    above = [MA_NAMES[n] for n in last if c > last[n]]
    below = [MA_NAMES[n] for n in last if c <= last[n]]
    text = (f"收盤 {c:,.0f}，" + (f"在{'、'.join(above)}之上" if above else "")
            + ("；" if above and below else "") + (f"在{'、'.join(below)}之下" if below else "")
            + f"。月線{slope.get(20, '—')}、季線{slope.get(60, '—')}"
            + (f"、年線{slope[240]}" if 240 in slope else "") + "。")
    items.append(("均線", text, 1 if len(above) > len(below) else -1 if len(below) > len(above) else 0))

    # 2. 位置與動能：距 60 日高點、近 5 日和 20 日漲跌
    hi60 = high.tail(60).max()
    r5 = (c / close.iloc[-6] - 1) * 100
    r20 = (c / close.iloc[-21] - 1) * 100
    items.append(("位置", f"距近 60 日高點 {hi60:,.0f} 還差 {(c / hi60 - 1) * 100:+.1f}%；近 5 日 {r5:+.1f}%、近 20 日 {r20:+.1f}%。",
                  1 if r20 > 0 else -1 if r20 < 0 else 0))

    # 3. 量能：5 日均量和 20 日均量比，配合近 5 日漲跌
    if value.tail(20).notna().all() and value.tail(20).mean() > 0:
        vr = (value.tail(5).mean() / value.tail(20).mean() - 1) * 100
        kind = ("價漲量增" if vr > 5 else "價漲量縮" if vr < -5 else "價漲量平") if r5 > 0 else \
               ("價跌量增，賣壓較重" if vr > 5 else "價跌量縮，賣壓不大" if vr < -5 else "價跌量平")
        items.append(("量能", f"近 5 日成交值比 20 日平均 {vr:+.0f}%，{kind}。",
                      1 if (r5 > 0 and vr > 5) else -1 if (r5 < 0 and vr > 5) else 0))

    # 4. 外資現股：近 5 日、20 日合計買賣超金額與連買連賣
    if foreign is not None and len(foreign) >= 5:
        f5, f20 = foreign.tail(5).sum(), foreign.tail(20).sum()
        sign, n = _streak(foreign.values)
        streak = f"，連續 {n} 天{'買超' if sign > 0 else '賣超'}" if sign and n >= 2 else ""
        items.append(("外資現股", f"近 5 日{'買超' if f5 >= 0 else '賣超'} {abs(f5):,.0f} 億、"
                                   f"近 {min(20, len(foreign))} 日{'買超' if f20 >= 0 else '賣超'} {abs(f20):,.0f} 億{streak}。",
                      1 if f5 > 0 else -1 if f5 < 0 else 0))

    # 5. 外資台指期：淨未平倉和 5 日、20 日前比
    fut = None
    if futures is not None and not futures.empty:
        fut = futures[futures["item"] == "外資及陸資"].sort_values("date")
        fut = pd.to_numeric(fut["net_oi"], errors="coerce").dropna().reset_index(drop=True)
    if fut is not None and len(fut) > 5:
        now, d5 = fut.iloc[-1], fut.iloc[-1] - fut.iloc[-6]
        d20 = fut.iloc[-1] - fut.iloc[-21] if len(fut) > 20 else None
        side = "淨多單" if now >= 0 else "淨空單"
        # 淨未平倉往上（空單減少或多單增加）偏多，往下偏空
        move = "空單減少" if now < 0 and d5 > 0 else "空單增加" if now < 0 and d5 < 0 else \
               "多單增加" if now >= 0 and d5 > 0 else "多單減少" if now >= 0 and d5 < 0 else "持平"
        items.append(("外資期貨", f"{side} {abs(now):,.0f} 口，近 5 日{move} {abs(d5):,.0f} 口"
                                  + (f"、近 20 日 {d20:+,.0f} 口" if d20 is not None else "") + "。",
                      1 if d5 > 0 else -1 if d5 < 0 else 0))

    # 6. 支撐與壓力：上下最近的均線（5 日線太近不算）
    levels = {MA_NAMES[n]: v for n, v in last.items() if n != 5}
    sup = max(((k, v) for k, v in levels.items() if v < c), key=lambda kv: kv[1], default=None)
    res = min(((k, v) for k, v in levels.items() if v > c), key=lambda kv: kv[1], default=None)
    parts = []
    if sup:
        parts.append(f"下方支撐看{sup[0]} {sup[1]:,.0f}（{(sup[1] / c - 1) * 100:+.1f}%）")
    if res:
        parts.append(f"上方壓力看{res[0]} {res[1]:,.0f}（{(res[1] / c - 1) * 100:+.1f}%）")
    if parts:
        items.append(("關鍵價位", "，".join(parts) + "。", 0))

    # 7. 明日買進的大盤濾網
    ok = 20 in last and 60 in last and c > last[20] and c > last[60]
    items.append(("選股濾網", "在月線和季線之上，「明日買進」綠燈，照規則可以買。" if ok
                  else "沒有同時站上月線和季線，「明日買進」紅燈，照規則不新買。", 1 if ok else -1))

    # 結論：長線看年線，中短線看月線、季線
    verdict, tone = _verdict(close)
    long_up = 240 in last and c > last[240] and slope.get(240) == "上揚"
    long_txt = "長線（年線）仍往上" if long_up else "長線（年線）轉弱" if 240 in last else "長線資料不足"
    short_txt = ("中短線站在月線和季線之上" if ok else
                 "中短線跌破月線" + ("、仍守季線" if 60 in last and c > last[60] else "和季線") if 20 in last and c < last[20]
                 else "中短線在月線之上、季線之下")
    summary = f"{verdict}：{long_txt}，{short_txt}。"
    return {"date": str(df["date"].iloc[-1])[:10], "verdict": verdict, "tone": tone, "summary": summary, "items": items,
            "history": verdict_history(df)}


# 色帶的顏色：多頭紅、空頭綠，回檔淺紅、反彈淺綠，盤整灰
VERDICT_COLORS = {"多頭": UP_COLOR, "長多格局中的回檔": "#fca5a5", "盤整": "#64748b",
                  "空頭中的反彈": "#86efac", "空頭": DOWN_COLOR}


def _history_html(history):
    """近 60 天趨勢色帶：每天一格，滑鼠移上去看日期和結論；下面寫最近一次轉變和各結論的天數"""
    if not history:
        return ""
    cells = "".join(
        f'<div title="{d}　{v}" style="flex:1;height:18px;background:{VERDICT_COLORS[v]};"></div>' for d, v, _ in history)
    counts = {}
    for _, v, _ in history:
        counts[v] = counts.get(v, 0) + 1
    legend = "".join(
        f'<span style="white-space:nowrap;margin-right:10px;"><span style="display:inline-block;width:10px;height:10px;'
        f'background:{VERDICT_COLORS[v]};border-radius:2px;margin-right:4px;"></span>{v} {counts[v]} 天</span>'
        for v in VERDICT_COLORS if v in counts)
    chg = last_change(history)
    chg_txt = (f"最近一次轉變：{chg[0]} 由「{chg[1]}」轉為「{chg[2]}」。" if chg
               else f"近 {len(history)} 個交易日都是「{history[-1][1]}」。")
    return (f'<div style="margin-top:10px;font-size:0.85rem;color:{TEXT};">近 {len(history)} 個交易日趨勢'
            f'<span style="color:{MUTED};">（{history[0][0]} ～ {history[-1][0]}）</span></div>'
            f'<div style="display:flex;gap:1px;margin:4px 0;border-radius:3px;overflow:hidden;">{cells}</div>'
            f'<div style="font-size:0.8rem;color:{TEXT};">{chg_txt}</div>'
            f'<div style="font-size:0.78rem;color:{MUTED};margin-top:2px;line-height:1.6;">{legend}</div>')


def render(result):
    """畫出大盤趨勢分析面板（result 是 analyze 的結果）"""
    if not result:
        st.info("大盤資料不足，暫時無法分析趨勢。")
        return
    color = lambda t: UP_COLOR if t > 0 else DOWN_COLOR if t < 0 else MUTED
    rows = "".join(
        f'<tr><td style="color:{color(t)};white-space:nowrap;padding:3px 12px 3px 0;vertical-align:top;">● {k}</td>'
        f'<td style="padding:3px 0;color:{TEXT};">{v}</td></tr>'
        for k, v, t in result["items"])
    st.markdown(
        f'<div style="background:{PANEL};border:1px solid {BORDER};border-radius:8px;padding:12px 16px;margin:6px 0 10px;">'
        f'<div style="font-size:1.05rem;font-weight:700;color:{color(result["tone"])};margin-bottom:6px;">'
        f'🧭 大盤趨勢：{result["summary"]}</div>'
        f'<table style="border-collapse:collapse;font-size:0.92rem;">{rows}</table>'
        f'{_history_html(result.get("history"))}'
        f'<div style="color:{MUTED};font-size:0.8rem;margin-top:6px;">'
        f'資料日期 {result["date"]}。依加權指數均線、成交值、外資現股與台指期淨未平倉推算的趨勢判斷，不是漲跌預測；'
        f'紅色偏多、綠色偏空、灰色中性。</div></div>',
        unsafe_allow_html=True)


def compute_market_trend():
    """讀資料並分析（LINE 的「大盤趨勢」也用這個）"""
    from utils.db import get_futures_ins, get_market_index, get_recent_closes, get_recent_ins
    index_df = get_market_index("TAIEX", INDEX_DAYS)
    ins = get_recent_ins(40, "stock_id, date, foreign_net_shares")
    closes = get_recent_closes(40, "stock_id, date, close_price", adjust=False)
    return analyze(index_df, foreign_amount(ins, closes), get_futures_ins())


@st.cache_data(ttl=3600)
def load_market_trend():
    """網頁用：首頁和總覽共用同一份快取"""
    return compute_market_trend()


def trend_text(result):
    """LINE 用的文字版（result 是 analyze 的結果）"""
    if not result:
        return "大盤資料不足，暫時無法分析趨勢。"
    mark = {1: "🔴", -1: "🟢", 0: "⚪"}
    lines = [f"🧭 大盤趨勢（{result['date']} 收盤）", "", result["summary"], ""]
    lines += [f"{mark[t]} {k}：{v}" for k, v, t in result["items"]]
    chg = last_change(result.get("history") or [])
    if chg:
        lines += ["", f"📅 最近一次轉變：{chg[0]} 由「{chg[1]}」轉為「{chg[2]}」。"]
    lines += ["", "🔴 偏多、🟢 偏空、⚪ 中性。依加權指數均線、成交值、外資現股與台指期淨未平倉推算的趨勢判斷，不是漲跌預測。",
              "同網頁首頁和全台股總覽的「🧭 大盤趨勢」。"]
    return "\n".join(lines)
