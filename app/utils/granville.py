import numpy as np
import pandas as pd

# 葛蘭碧八大法則：用收盤價與移動平均線（均線）的相對位置、均線方向判斷買賣點
# 每條法則：(訊號, 說明, 可靠度)
# 可靠度依 2025-09 ~ 2026-10 回測（20 日均量 500 張以上、持有 10 日相對大盤的超額報酬），
# 分成前後兩段期間檢驗：兩段方向一致的標「穩定」，方向相反或樣本太少的標「參考」。
RULES = {
    1: ("買進", "均線由下彎轉為走平或上揚，股價由下往上突破均線", "參考"),
    2: ("買進", "均線持續上揚（且月線在季線之上），股價短暫跌破均線後又站回均線之上", "穩定"),
    3: ("買進", "均線持續上揚，股價在均線之上回檔、靠近均線但沒跌破，再度上漲", "參考"),
    4: ("買進", "均線下彎，股價急跌、遠低於均線（負乖離過大），容易反彈", "參考"),
    5: ("賣出", "均線由上揚轉為走平或下彎，股價由上往下跌破均線", "參考"),
    6: ("賣出", "均線持續下彎，股價短暫突破均線後又跌回均線之下", "穩定"),
    7: ("賣出", "均線持續下彎，股價在均線之下反彈、靠近均線但沒突破，再度下跌", "穩定"),
    # 原典把正乖離過大視為賣訊，但回測兩段期間都是續漲（乖離越大漲越多），所以只當過熱提醒
    8: ("過熱", "均線上揚，股價急漲、遠高於均線（正乖離過大）；回測多半續漲，僅提醒追高風險", "穩定"),
}

# 預設只顯示回測穩定的法則
STABLE_RULES = [n for n, (_, _, level) in RULES.items() if level == "穩定"]


def granville_signals(prices, ma_days=20, slope_days=5, lookback=5,
                      near_pct=2.0, bias_pct=10.0, trend_days=60):
    """
    依葛蘭碧八大法則，判斷每檔股票在最新一個交易日符合哪幾條。

    prices：含 stock_id、date、close_price 欄位，每檔股票每天一筆。
    ma_days：均線天數。
    slope_days：均線方向用「今天的均線 − slope_days 天前的均線」判斷，> 0 上揚、< 0 下彎。
    lookback：法則 2、3、6、7 往回看幾天內的走勢。
    near_pct：法則 3、7「靠近均線」的距離（乖離率 %）。
    bias_pct：法則 4、8「乖離過大」的門檻（乖離率 %）。
    trend_days：長期均線（季線）天數。法則 2 另外要求均線在季線之上，回測兩段期間都比較好；
                資料不足算不出季線的股票不套用這個條件。

    回傳每檔股票一筆：收盤價、漲跌、均線、乖離率、均線方向、20 日均量（張）、符合的法則（list）。
    資料不足的股票（例如新上市）不列出。
    """
    if prices.empty:
        return pd.DataFrame()

    close = prices.assign(close_price=pd.to_numeric(prices["close_price"], errors="coerce")) \
        .pivot_table(index="date", columns="stock_id", values="close_price", aggfunc="last") \
        .sort_index()
    ma = close.rolling(ma_days, min_periods=ma_days).mean()
    ma_long = close.rolling(trend_days, min_periods=trend_days).mean()
    slope = ma - ma.shift(slope_days)
    bias = (close - ma) / ma * 100

    # 「今天」與往回看的區間（不含今天）
    c_t, c_y = close.iloc[-1], close.iloc[-2]
    ma_t, ma_y = ma.iloc[-1], ma.iloc[-2]
    bias_t = bias.iloc[-1]
    slope_t = slope.iloc[-1]
    slope_before = slope.iloc[-1 - slope_days]  # 一段時間前的均線方向，判斷「轉折」用
    window = bias.iloc[-1 - lookback:-1]
    above_long = ma_long.iloc[-1].isna() | (ma_t > ma_long.iloc[-1])

    if "volume" in prices.columns:
        vol = prices.assign(volume=pd.to_numeric(prices["volume"], errors="coerce")) \
            .pivot_table(index="date", columns="stock_id", values="volume", aggfunc="last") \
            .reindex(index=close.index, columns=close.columns)
        avg_vol20 = (vol.fillna(0).tail(20).mean() / 1000).round(0)  # 股 → 張
    else:
        avg_vol20 = pd.Series(np.nan, index=close.columns)

    rising = slope_t > 0
    falling = slope_t < 0
    cross_up = (c_y <= ma_y) & (c_t > ma_t)
    cross_down = (c_y >= ma_y) & (c_t < ma_t)

    hits = {
        1: cross_up & (slope_before <= 0) & (slope_t >= 0),
        2: rising & (slope_before > 0) & (window.min() < 0) & (c_t > ma_t) & above_long,
        3: rising & (window.min() > 0) & (window.min() <= near_pct)
           & (bias_t > 0) & (c_t > c_y),
        4: falling & (bias_t <= -bias_pct),
        5: cross_down & (slope_before >= 0) & (slope_t <= 0),
        6: falling & (slope_before < 0) & (window.max() > 0) & (c_t < ma_t),
        7: falling & (window.max() < 0) & (window.max() >= -near_pct)
           & (bias_t < 0) & (c_t < c_y),
        8: rising & (bias_t >= bias_pct),
    }

    out = pd.DataFrame({
        "stock_id": close.columns,
        "close_price": c_t.values,
        "change": (c_t - c_y).round(2).values,
        "ma": ma_t.round(2).values,
        "bias": bias_t.round(2).values,
        "ma_trend": np.select([rising, falling], ["上揚", "下彎"], "走平"),
        "avg_volume_20": avg_vol20.values,
    })
    # 今天沒有收盤價或均線算不出來（資料不足）的不判斷
    valid = (c_t.notna() & ma_t.notna() & slope_before.notna()).values
    out = out[valid].reset_index(drop=True)
    valid_ids = close.columns[valid]
    out["rules"] = [
        [n for n, hit in hits.items() if bool(hit.get(sid, False))]
        for sid in valid_ids
    ]
    return out
