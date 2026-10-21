import pandas as pd
import numpy as np

def calc_ma(df, periods=[5, 10, 20, 60, 120, 240]):
    """計算移動平均線"""
    for p in periods:
        if len(df) >= p:
            df[f"MA{p}"] = df["close_price"].rolling(window=p).mean().round(2)
        else:
            df[f"MA{p}"] = None
    return df

# ===== 技術指標分級：(下限, 名稱, 燈號, 說明)，由高往低比對。燈號 bull 紅（偏多）、bear 綠（過熱或偏弱）、neu 金 =====
# 依回測（2024-01～2026-10，20 日均量 ≥ 500 張的股票，之後 20 個交易日相對大盤的超額報酬；全部股票平均約 −1.5%）。
# 高檔的股票之後反而比較強，所以 RSI、KD 只有 90 以上才標過熱的「超買」，70／80～90 標「強勢」
INF = float("-inf")

# RSI：70～80 −0.75%、80 以上 +0.35%、50～70 −1.42%、30～50 −1.79%、30 以下 −1.68%
RSI_LEVELS = [
    (90, "超買", "bear", "漲得太急，短線過熱，小心拉回"),
    (70, "強勢", "bull", "買盤強，回測中之後常比一般股票強"),
    (50, "偏強", "neu", "多方稍占上風"),
    (30, "偏弱", "neu", "空方稍占上風"),
    (INF, "超賣", "bear", "跌得很深，但不代表會反彈"),
]
# KD（看 K 值）：80 以上 +0.02%（連續 3 天 +0.53%）、20 以下 −1.97%
KD_LEVELS = [
    (90, "超買", "bear", "短線過熱，小心拉回"),
    (80, "強勢", "bull", "買盤強，回測中之後常比一般股票強"),
    (50, "偏強", "neu", "多方稍占上風"),
    (20, "偏弱", "neu", "空方稍占上風"),
    (INF, "超賣", "bear", "跌得很深，但不代表會反彈"),
]
# 52 週區間位置（%）：90 以上 +0.53%、70～90 −0.56%、50～70 −1.65%、30～50 −2.28%、10～30 −2.27%、10 以下 −3.15%
POSITION_LEVELS = [
    (90, "接近年高", "bull", "強勢股常續強，回測中之後比一般股票強"),
    (70, "偏高", "bull", "在年區間上段，走勢偏強"),
    (50, "中上段", "neu", "在年區間中間偏上"),
    (10, "中下段", "bear", "在年區間下半段，回測中之後偏弱"),
    (INF, "接近年低", "bear", "弱勢股常續弱，便宜不代表會漲"),
]
# 布林通道位置（%，0 是下軌、100 是上軌）：突破上軌 −0.64%、靠近上軌 −0.83%、中段 −1.64%、靠近下軌 −1.99%、
# 跌破下軌 −0.92%（但前後兩段時間差很多，不穩定）
BOLL_LEVELS = [
    (100, "突破上軌", "bull", "走勢強，回測中之後常比一般股票強"),
    (80, "靠近上軌", "bull", "走勢偏強"),
    (20, "通道中段", "neu", "在正常波動範圍中間"),
    (0, "靠近下軌", "bear", "走勢偏弱，回測中之後表現最差"),
    (INF, "跌破下軌", "neu", "跌深，偶有短線反彈但不穩定"),
]
# 20 日乖離率（%）：10 以上 −0.30%～+0.20%、5～10 −0.95%、0～5 −1.61%、−10～0 約 −2.0%、
# −10 以下 +0.18%（但前後兩段時間一正一負，不穩定）
BIAS_LEVELS = [
    (10, "大幅正乖離", "bull", "離月線很遠，但回測中強勢股常續強，不必急著賣"),
    (5, "正乖離", "neu", "在月線上方，走勢偏強"),
    (0, "站上月線", "neu", "貼近月線上方"),
    (-10, "跌破月線", "bear", "在月線下方，回測中之後偏弱"),
    (INF, "大幅負乖離", "neu", "跌深離月線很遠，偶有短線反彈但不穩定"),
]
LAMP = {name: lamp for levels in (RSI_LEVELS, KD_LEVELS) for _, name, lamp, _ in levels}


def level(levels, value):
    """數值 → (名稱, 燈號, 說明)；算不出來（NaN）回傳 ("N/A", "neu", "")"""
    for low, name, lamp, note in levels:
        if value >= low:
            return name, lamp, note
    return "N/A", "neu", ""


def rsi_level(rsi):
    """RSI → (名稱, 說明)"""
    name, _, note = level(RSI_LEVELS, rsi)
    return name, note


def volume_level(ratio, change):
    """量比（當日量 ÷ 前 20 日均量）＋當日漲跌 → (名稱, 燈號, 說明)
    回測：價漲量增（量比 1.5 以上）−1.05%，稍好；其他（爆量、價跌量增、量縮）都和一般股票差不多"""
    if ratio >= 3:
        return "爆量", "neu", "成交量是平常 3 倍以上，回測中對之後漲跌影響不大"
    if ratio >= 1.5:
        if change >= 0:
            return "價漲量增", "bull", "很多人搶著買，回測中之後稍好於一般股票"
        return "價跌量增", "neu", "賣壓較重，但回測中對之後漲跌影響不大"
    if ratio < 0.7:
        return "量縮", "neu", "交易清淡"
    return "量能正常", "neu", "成交量和平常差不多"


def calc_rsi(df, period=14):
    """計算 RSI"""
    if len(df) < period + 1:
        df["RSI"] = None
        return df
    delta = df["close_price"].diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    df["RSI"] = (100 - (100 / (1 + rs))).round(2)
    return df

def calc_macd(df, fast=12, slow=26, signal=9):
    """計算 MACD"""
    if len(df) < slow + signal:
        df["MACD"] = None
        df["MACD_SIGNAL"] = None
        df["MACD_HIST"] = None
        return df
    ema_fast = df["close_price"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close_price"].ewm(span=slow, adjust=False).mean()
    df["MACD"] = (ema_fast - ema_slow).round(2)
    df["MACD_SIGNAL"] = df["MACD"].ewm(span=signal, adjust=False).mean().round(2)
    df["MACD_HIST"] = (df["MACD"] - df["MACD_SIGNAL"]).round(2)
    return df

def calc_kd(df, period=9):
    """計算 KD 指標"""
    if len(df) < period:
        df["K"] = None
        df["D"] = None
        return df
    low_min  = df["low_price"].rolling(window=period).min()
    high_max = df["high_price"].rolling(window=period).max()
    rsv = (df["close_price"] - low_min) / (high_max - low_min) * 100
    df["K"] = rsv.ewm(com=2, adjust=False).mean().round(2)
    df["D"] = df["K"].ewm(com=2, adjust=False).mean().round(2)
    return df

def calc_bollinger(df, period=20, std=2):
    """計算布林通道"""
    if len(df) < period:
        df["BB_MID"] = None
        df["BB_UP"]  = None
        df["BB_LOW"] = None
        return df
    df["BB_MID"] = df["close_price"].rolling(window=period).mean().round(2)
    bb_std = df["close_price"].rolling(window=period).std()
    df["BB_UP"]  = (df["BB_MID"] + std * bb_std).round(2)
    df["BB_LOW"] = (df["BB_MID"] - std * bb_std).round(2)
    return df

def calc_obv(df):
    """能量潮 OBV：收盤上漲當天加上成交量、下跌減去，持平不變，一路累加"""
    if "volume" not in df.columns:
        df["OBV"] = None
        return df
    direction = np.sign(pd.to_numeric(df["close_price"], errors="coerce").diff()).fillna(0)
    df["OBV"] = (direction * pd.to_numeric(df["volume"], errors="coerce").fillna(0)).cumsum()
    return df


# OBV 和股價近 20 日的新高、新低（回測：2024-01～2026-10，之後 20 日相對大盤，全部平均約 −1.5%）
# 量價齊揚 −0.65%、OBV 先破底但股價還沒 −2.5%（前後兩段時間都最差）、頂背離 −0.76%（不是賣訊號）、
# 底背離 −1.9%（不是買訊號）、量價齊跌 −1.7%、量先價行 −1.25%（差別不大）
OBV_DAYS = 20


def obv_state(close, obv, n=OBV_DAYS):
    """收盤價和 OBV 序列 → (名稱, 燈號, 說明)；資料不夠回傳 None"""
    c = pd.to_numeric(close, errors="coerce")
    o = pd.to_numeric(obv, errors="coerce")
    if c.notna().sum() <= n or o.notna().sum() <= n:
        return None
    p_hi, p_lo = c.iloc[-1] >= c.iloc[-n:].max(), c.iloc[-1] <= c.iloc[-n:].min()
    o_hi, o_lo = o.iloc[-1] >= o.iloc[-n:].max(), o.iloc[-1] <= o.iloc[-n:].min()
    if p_hi and o_hi:
        return "量價齊揚", "bull", "股價和 OBV 都創 20 日新高，回測中之後比一般股票強"
    if o_lo and not p_lo:
        return "OBV 先破底", "bear", "成交量先轉弱、股價還沒跌破，回測中之後表現最差，要小心"
    if p_lo and o_lo:
        return "量價齊跌", "bear", "股價和 OBV 都創 20 日新低，走勢偏弱"
    if p_hi:
        return "頂背離", "neu", "股價創新高但 OBV 沒有；回測中之後並沒有比較差，不是賣訊號"
    if p_lo:
        return "底背離", "neu", "股價創新低但 OBV 沒有；回測中之後並沒有比較好，不是買訊號"
    if o_hi:
        return "量先價行", "neu", "OBV 先創新高、股價還沒；回測中效果不明顯"
    return "一般", "neu", "近 20 日股價和 OBV 都沒有創新高或新低"


def macd_zero(dif, signal):
    """MACD 零軸位置 → (名稱, 燈號)。回測：DIF、訊號線都在零軸上 −1.1%，都在零軸下 −1.8%（全部平均約 −1.5%）"""
    if dif is None or signal is None or pd.isna(dif) or pd.isna(signal):
        return None
    if dif > 0 and signal > 0:
        return "零軸上，多頭", "bull"
    if dif < 0 and signal < 0:
        return "零軸下，空頭", "bear"
    return "正在穿越零軸", "neu"


def fill_no_trade(df):
    """
    停牌或當天沒有成交時，資料庫的開高低收是空值。依交易所慣例視為股價不變：
    收盤沿用前一個成交日，開高低等於收盤、成交量為 0，並用 no_trade 欄位標記這些日子。
    這樣均線、RSI 等指標不會因為一天空值，整段（例如 20 日均線就是 20 天）都算不出來。
    """
    df = df.copy()
    close = pd.to_numeric(df["close_price"], errors="coerce")
    df["no_trade"] = close.isna()
    df["close_price"] = close.ffill()
    for col in ("open_price", "high_price", "low_price"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").where(~df["no_trade"], df["close_price"])
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").where(~df["no_trade"], 0)
    return df

def calc_all(df):
    """計算所有技術指標（無成交的日子先依 fill_no_trade 補成股價不變）"""
    if df.empty:
        return df
    df = fill_no_trade(df)
    if len(df) < 5:
        return df
    df = calc_ma(df)
    df = calc_rsi(df)
    df = calc_macd(df)
    df = calc_kd(df)
    df = calc_bollinger(df)
    df = calc_obv(df)
    return df

def get_latest_signals(df):
    """取得最新技術指標數值與訊號"""
    if df.empty:
        return {}

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last

    signals = {}

    # 均線
    close = last.get("close_price", 0)
    for ma in [5, 10, 20, 60, 120, 240]:
        key = f"MA{ma}"
        val = last.get(key)
        if val and close:
            signals[key] = {
                "value": round(val, 2),
                "above": close > val,   # 股價在均線上方
            }

    # RSI
    if last.get("RSI") is not None:
        rsi = last["RSI"]
        status, note = rsi_level(rsi)
        signals["RSI"] = {
            "value": rsi,
            "status": status,
            "note": note,
        }

    # MACD
    if last.get("MACD") is not None:
        signals["MACD"] = {
            "value":  last["MACD"],
            "signal": last["MACD_SIGNAL"],
            "hist":   last["MACD_HIST"],
            "golden_cross": last["MACD"] > last["MACD_SIGNAL"] and prev["MACD"] <= prev["MACD_SIGNAL"],
            "dead_cross":   last["MACD"] < last["MACD_SIGNAL"] and prev["MACD"] >= prev["MACD_SIGNAL"],
        }

    # KD
    if last.get("K") is not None:
        status, _, note = level(KD_LEVELS, last["K"])
        signals["KD"] = {
            "K": last["K"],
            "D": last["D"],
            "status": status,
            "note": note,
        }

    # 布林通道
    if last.get("BB_MID") is not None:
        signals["Bollinger"] = {
            "mid": last["BB_MID"],
            "up":  last["BB_UP"],
            "low": last["BB_LOW"],
            "width": round((last["BB_UP"] - last["BB_LOW"]) / last["BB_MID"] * 100, 2),
            "position": "上軌以上" if close > last["BB_UP"] else (
                         "下軌以下" if close < last["BB_LOW"] else "通道內"),
        }

    return signals