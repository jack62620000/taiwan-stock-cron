"""
K線看盤：把股價、指數、還原權值事件整理成圖表網頁要的格式。
圖表本身在 kline_chart.html（TradingView Lightweight Charts），這裡只負責把資料填進去。
"""
import json
from pathlib import Path

import pandas as pd

_TEMPLATE = Path(__file__).with_name("kline_chart.html")

# 指數代號 → (名稱, 標籤)
INDEXES = {"TAIEX": ("加權指數", "上市"), "TPEX": ("櫃買指數", "上櫃")}


def _yymmdd(d):
    """2026-10-27 → 261027（網頁再轉回日期，資料量少一半）"""
    s = str(d)[:10]
    return int(s[2:4] + s[5:7] + s[8:10])


def _num(x):
    x = round(float(x), 2)
    return int(x) if x.is_integer() else x


def _series(df, cols, vol_div):
    """cols：開、高、低、收、量 的欄位名稱；vol_div：成交量換算單位（股 → 張是 1000）"""
    o, h, l, c, v = cols
    df = df.copy()
    for col in (o, h, l, c, v):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=[o, h, l, c])
    # 沒成交的日子開高低收是 0，畫出來會變成一根直到底的 K 棒，跳過
    df = df[(df[c] > 0) & (df[l] > 0)].sort_values("date")
    return {
        "d": [_yymmdd(x) for x in df["date"]],
        "o": [_num(x) for x in df[o]],
        "h": [_num(x) for x in df[h]],
        "l": [_num(x) for x in df[l]],
        "c": [_num(x) for x in df[c]],
        "v": [int(round(x / vol_div)) for x in df[v].fillna(0)],
    }


def build_payload(stock_ids, stock_list, prices, events, indexes, asof):
    """
    stock_ids：要顯示的股票（依清單順序）；stock_list：get_stock_list() 的結果（名稱、產業）
    prices：daily_prices 原始價格；events：price_adjustments（stock_id, date, event_type, factor）
    indexes：{"TAIEX": DataFrame, "TPEX": DataFrame}，market_index 原始資料
    沒有任何資料時回傳 None
    """
    names, industries = {}, {}
    if not stock_list.empty:
        sl = stock_list.drop_duplicates("stock_id")
        names = {k: v for k, v in zip(sl["stock_id"], sl.get("stock_name", sl["stock_id"])) if pd.notna(v)}
        if "industry" in sl.columns:
            industries = {k: v for k, v in zip(sl["stock_id"], sl["industry"]) if pd.notna(v)}
    meta, series, order = {}, {}, []

    for code, (name, tag) in INDEXES.items():
        df = indexes.get(code)
        if df is None or df.empty:
            continue
        df = df.copy()
        df["trade_value"] = df.get("trade_value", 0)
        s = _series(df, ["open", "high", "low", "close", "trade_value"], 1e8)  # 成交值：億元
        if s["d"]:
            meta[code] = {"name": name, "ind": tag, "type": "index"}
            series[code] = s
            order.append(code)

    by_stock = dict(tuple(prices.groupby("stock_id"))) if not prices.empty else {}
    first = None
    for sid in stock_ids:
        if sid in meta or sid not in by_stock:
            continue
        s = _series(by_stock[sid], ["open_price", "high_price", "low_price", "close_price", "volume"], 1000)
        if not s["d"]:
            continue
        meta[sid] = {"name": names.get(sid, sid), "ind": industries.get(sid, ""), "type": "stock"}
        series[sid] = s
        order.append(sid)
        first = first or sid

    if not order:
        return None

    adj = {}
    if events is not None and not events.empty:
        ev = events.copy()
        ev["factor"] = pd.to_numeric(ev["factor"], errors="coerce")
        ev = ev[(ev["factor"] > 0) & ev["stock_id"].isin(series.keys())].sort_values("date")
        for sid, g in ev.groupby("stock_id"):
            adj[sid] = [[_yymmdd(d), round(float(f), 8), t or ""]
                        for d, f, t in zip(g["date"], g["factor"], g["event_type"])]

    return {"meta": meta, "s": series, "adj": adj, "order": order,
            "first": first or order[0], "asof": str(asof or "")[:10]}


def build_html(payload):
    """把資料填進圖表網頁；</ 要跳脫，免得名稱裡的字串提早結束 <script>"""
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return _TEMPLATE.read_text(encoding="utf-8").replace("/*DATA*/", data)
