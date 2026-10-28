"""
個股分析卡的互動 K 線圖（Lightweight Charts，套件 streamlit-lightweight-charts）

build_charts 只組出圖表設定（list of dict），不碰 Streamlit，方便測試；頁面再交給 renderLightweightCharts 畫。
上下幾張圖共用時間軸：主圖 K 線＋均線／布林通道＋葛蘭碧買賣點標記＋價位線，下面是成交量和一張副圖。
"""
import pandas as pd

from utils.granville import RULES, STABLE_RULES, rule_frames
from utils.ui import ACCENT, BG_COLOR, BORDER, DOWN_COLOR, GLOW, MUTED, TEXT, UP_COLOR

NO_TRADE_COLOR = "#9ca3af"
MA_COLORS = {"MA5": ACCENT, "MA20": GLOW, "MA60": "#a78bfa"}
BB_COLOR = "#64748b"
SUB_CHARTS = ["外資買賣超", "MACD", "KD"]


def _chart(height, time_visible=False):
    return {
        "height": height,
        "layout": {"background": {"type": "solid", "color": BG_COLOR}, "textColor": TEXT, "fontSize": 12},
        "grid": {"vertLines": {"color": BORDER + "55"}, "horzLines": {"color": BORDER + "55"}},
        "rightPriceScale": {"borderColor": BORDER, "minimumWidth": 72},
        "timeScale": {"borderColor": BORDER, "visible": time_visible},
        "crosshair": {"mode": 0},
        # 指定語系：瀏覽器語系設定特殊時（例如 en-US@posix）圖會畫不出來
        "localization": {"locale": "zh-TW", "dateFormat": "yyyy/MM/dd"},
    }


def _line(df, col, color, title, width=2, style=0):
    data = [{"time": t, "value": round(float(v), 2)} for t, v in zip(df["time"], df[col]) if pd.notna(v)]
    return {"type": "Line", "data": data,
            "options": {"color": color, "lineWidth": width, "lineStyle": style, "title": title,
                        "priceLineVisible": False, "lastValueVisible": False, "crosshairMarkerVisible": False}}


def _hist(df, col, colors, title, fmt=None):
    data = [{"time": t, "value": float(v), "color": c}
            for t, v, c in zip(df["time"], df[col], colors) if pd.notna(v)]
    opts = {"title": title, "priceLineVisible": False}
    if fmt:
        opts["priceFormat"] = fmt
    return {"type": "Histogram", "data": data, "options": opts}


def granville_markers(df, full_close, rules=tuple(STABLE_RULES)):
    """
    圖上的葛蘭碧標記（預設只標回測穩定的法則，和「均線買賣點」預設相同）：
    買進法則在 K 棒下方紅色 ▲、賣出法則在上方綠色 ▼、過熱（法則 8）上方灰點。
    full_close：完整期間的收盤價（以日期為索引），均線要用比圖更長的資料才算得出來。
    """
    f = rule_frames(full_close.to_frame("s"))
    shown = set(df["date"])
    out = []
    for d in full_close.index:
        if d not in shown:
            continue
        hit = [n for n in rules if bool(f["hits"][n].at[d, "s"])]
        if not hit:
            continue
        sides = {RULES[n][0] for n in hit}
        t = d   # 日期字串 YYYY-MM-DD 就是圖的時間
        label = "、".join(str(n) for n in hit)
        if "買進" in sides:
            out.append({"time": t, "position": "belowBar", "color": UP_COLOR, "shape": "arrowUp",
                        "text": f"買{label}" if sides == {"買進"} else label})
        elif "賣出" in sides:
            out.append({"time": t, "position": "aboveBar", "color": DOWN_COLOR, "shape": "arrowDown", "text": f"賣{label}"})
        else:
            out.append({"time": t, "position": "aboveBar", "color": MUTED, "shape": "circle", "text": "過熱"})
    return out


def build_charts(prices_ta, days, overlays=("均線",), sub="外資買賣超", ins=None, levels=None, markers=True):
    """
    prices_ta：個股分析卡算好指標的日線（date、open/high/low/close_price、volume、no_trade、MA5/20/60、BB_UP/LOW、K、D、MACD_HIST）；
    days：顯示最近幾個交易日；overlays：主圖要疊的「均線」「布林通道」；sub：副圖（SUB_CHARTS 其中之一或 None）；
    ins：法人買賣超（date、foreign_net_shares、sitca_net_shares）；levels：主圖水平價位線 {名稱: (價格, 顏色)}。
    回傳 renderLightweightCharts 的 charts 設定。
    """
    full = prices_ta.copy()
    full["date"] = full["date"].astype(str)
    for c in ["open_price", "high_price", "low_price", "close_price", "volume"]:
        full[c] = pd.to_numeric(full[c], errors="coerce")
    df = full.tail(days).reset_index(drop=True)
    df["time"] = df["date"]
    no_trade = df["no_trade"].fillna(False).astype(bool) if "no_trade" in df.columns else pd.Series(False, index=df.index)
    up = df["close_price"] >= df["open_price"]
    bar_color = [NO_TRADE_COLOR if n else UP_COLOR if u else DOWN_COLOR for n, u in zip(no_trade, up)]

    candles = []
    for r, color in zip(df.itertuples(), bar_color):
        if pd.isna(r.close_price):
            continue
        o = r.open_price if pd.notna(r.open_price) else r.close_price
        h = r.high_price if pd.notna(r.high_price) else max(o, r.close_price)
        lo = r.low_price if pd.notna(r.low_price) else min(o, r.close_price)
        candles.append({"time": r.time, "open": round(float(o), 2), "high": round(float(h), 2),
                        "low": round(float(lo), 2), "close": round(float(r.close_price), 2),
                        "color": color, "borderColor": color, "wickColor": color})
    price = {"type": "Candlestick", "data": candles,
             "options": {"upColor": UP_COLOR, "downColor": DOWN_COLOR, "borderVisible": False,
                         "wickUpColor": UP_COLOR, "wickDownColor": DOWN_COLOR}}
    if markers:
        full_close = full.set_index("date")["close_price"].where(~full.set_index("date")["no_trade"].fillna(False).astype(bool)) \
            if "no_trade" in full.columns else full.set_index("date")["close_price"]
        price["markers"] = granville_markers(df, full_close)
    main = [price]
    # 水平價位線：這個套件不支援 createPriceLine，改用整段同一個值的虛線，右側標出名稱和價格
    for name, (p, c) in (levels or {}).items():
        if p is not None and pd.notna(p):
            line = _line(df.assign(lv=float(p)), "lv", c, name, 1, 2)
            line["options"]["lastValueVisible"] = True
            main.append(line)
    if "均線" in overlays:
        main += [_line(df, c, MA_COLORS[c], c) for c in MA_COLORS if c in df.columns and df[c].notna().any()]
    if "布林通道" in overlays and "BB_UP" in df.columns and df["BB_UP"].notna().any():
        main += [_line(df, "BB_UP", BB_COLOR, "布林上軌", 1, 2), _line(df, "BB_LOW", BB_COLOR, "布林下軌", 1, 2)]

    vol = df.assign(lots=df["volume"] / 1000)
    charts = [
        {"chart": _chart(360), "series": main},
        {"chart": _chart(110, time_visible=sub is None),
         "series": [_hist(vol, "lots", bar_color, "成交量（張）", {"type": "volume"})]},
    ]
    sub_series = None
    if sub == "外資買賣超" and ins is not None and not ins.empty and "foreign_net_shares" in ins.columns:
        f = ins[["date", "foreign_net_shares"]].copy()
        f["date"] = f["date"].astype(str)
        f = df[["date", "time"]].merge(f, on="date", how="left")
        f["lots"] = pd.to_numeric(f["foreign_net_shares"], errors="coerce") / 1000
        sub_series = [_hist(f, "lots", [UP_COLOR if v >= 0 else DOWN_COLOR for v in f["lots"].fillna(0)],
                            "外資買賣超（張）", {"type": "volume"})]
    elif sub == "MACD" and "MACD_HIST" in df.columns:
        h = pd.to_numeric(df["MACD_HIST"], errors="coerce")
        sub_series = [_hist(df.assign(h=h), "h", [UP_COLOR if v >= 0 else DOWN_COLOR for v in h.fillna(0)], "MACD 柱")]
    elif sub == "KD" and "K" in df.columns:
        sub_series = [_line(df.assign(k=pd.to_numeric(df["K"], errors="coerce")), "k", ACCENT, "K"),
                      _line(df.assign(d=pd.to_numeric(df["D"], errors="coerce")), "d", GLOW, "D")]
    if sub_series:
        charts.append({"chart": _chart(130, time_visible=True), "series": sub_series})
    else:
        charts[1]["chart"]["timeScale"]["visible"] = True
    return charts
