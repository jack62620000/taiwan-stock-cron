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

# 回測（2024-01～2026-10，20 日均量 ≥ 500 張）：RSI > 70、K > 80 之後 20 日相對大盤反而比平均好 1.5～1.8 個百分點，
# RSI < 30、K < 20 之後比平均差，所以高檔叫「強勢區」、低檔叫「弱勢區」，不叫會反轉的「超買／超賣」
STRONG, WEAK, NORMAL = "強勢區", "弱勢區", "正常"


def zone(value, high, low):
    """指標高於 high 算強勢區、低於 low 算弱勢區"""
    return STRONG if value > high else WEAK if value < low else NORMAL


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
        signals["RSI"] = {
            "value": rsi,
            "status": zone(rsi, 70, 30),
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
        signals["KD"] = {
            "K": last["K"],
            "D": last["D"],
            "status": zone(last["K"], 80, 20),
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