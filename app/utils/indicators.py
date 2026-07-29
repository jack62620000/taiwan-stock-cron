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

def calc_all(df):
    """計算所有技術指標"""
    if df.empty or len(df) < 5:
        return df
    df = df.copy()
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
            "status": "超買" if rsi > 70 else ("超賣" if rsi < 30 else "正常"),
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
            "status": "超買" if last["K"] > 80 else ("超賣" if last["K"] < 20 else "正常"),
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