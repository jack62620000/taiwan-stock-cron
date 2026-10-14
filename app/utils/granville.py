import numpy as np
import pandas as pd

# 葛蘭碧八大法則：用收盤價與移動平均線（均線）的相對位置、均線方向判斷買賣點
RULES = {
    1: ("買進", "均線由下彎轉為走平或上揚，股價由下往上突破均線"),
    2: ("買進", "均線持續上揚，股價短暫跌破均線後又站回均線之上"),
    3: ("買進", "均線持續上揚，股價在均線之上回檔、靠近均線但沒跌破，再度上漲"),
    4: ("買進", "均線下彎，股價急跌、遠低於均線（負乖離過大），容易反彈"),
    5: ("賣出", "均線由上揚轉為走平或下彎，股價由上往下跌破均線"),
    6: ("賣出", "均線持續下彎，股價短暫突破均線後又跌回均線之下"),
    7: ("賣出", "均線持續下彎，股價在均線之下反彈、靠近均線但沒突破，再度下跌"),
    8: ("賣出", "均線上揚，股價急漲、遠高於均線（正乖離過大），容易回檔"),
}


def granville_signals(prices, ma_days=20, slope_days=5, lookback=5,
                      near_pct=2.0, bias_pct=10.0):
    """
    依葛蘭碧八大法則，判斷每檔股票在最新一個交易日符合哪幾條。

    prices：含 stock_id、date、close_price 欄位，每檔股票每天一筆。
    ma_days：均線天數。
    slope_days：均線方向用「今天的均線 − slope_days 天前的均線」判斷，> 0 上揚、< 0 下彎。
    lookback：法則 2、3、6、7 往回看幾天內的走勢。
    near_pct：法則 3、7「靠近均線」的距離（乖離率 %）。
    bias_pct：法則 4、8「乖離過大」的門檻（乖離率 %）。

    回傳每檔股票一筆：收盤價、漲跌、均線、乖離率、均線方向、符合的法則（list）。
    資料不足的股票（例如新上市）不列出。
    """
    if prices.empty:
        return pd.DataFrame()

    close = prices.assign(close_price=pd.to_numeric(prices["close_price"], errors="coerce")) \
        .pivot_table(index="date", columns="stock_id", values="close_price", aggfunc="last") \
        .sort_index()
    ma = close.rolling(ma_days, min_periods=ma_days).mean()
    slope = ma - ma.shift(slope_days)
    bias = (close - ma) / ma * 100

    # 「今天」與往回看的區間（不含今天）
    c_t, c_y = close.iloc[-1], close.iloc[-2]
    ma_t, ma_y = ma.iloc[-1], ma.iloc[-2]
    bias_t = bias.iloc[-1]
    slope_t = slope.iloc[-1]
    slope_before = slope.iloc[-1 - slope_days]  # 一段時間前的均線方向，判斷「轉折」用
    window = bias.iloc[-1 - lookback:-1]

    rising = slope_t > 0
    falling = slope_t < 0
    cross_up = (c_y <= ma_y) & (c_t > ma_t)
    cross_down = (c_y >= ma_y) & (c_t < ma_t)

    hits = {
        1: cross_up & (slope_before <= 0) & (slope_t >= 0),
        2: rising & (slope_before > 0) & (window.min() < 0) & (c_t > ma_t),
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
