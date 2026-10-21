"""
暫時的研究腳本（只讀資料庫）：RSI、KD「超買／超賣」與 MACD、KD 交叉之後的股價表現。
和網站相同的算法：RSI(14) 簡單平均、KD(9,3,3)、MACD(12,26,9)。
進場以「訊號當天收盤確認、隔天收盤」為基準，看之後 5／10／20 個交易日相對大盤的超額報酬。
只算 20 日均量 ≥ 500 張的股票。用法：python .github/probe/overbought.py
"""
import os
import sys
import time

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import signals  # noqa: E402
from momentum import STOCK_PATTERN  # noqa: E402

LIQ = 500 * 1000
HORIZONS = [5, 10, 20]
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)


def load(sb):
    dates = signals.trading_dates(sb)
    print(f"載入 {len(dates)} 個交易日（{dates[0]} ～ {dates[-1]}）的股價…")
    t = time.time()
    prices = signals.load_daily(sb, "daily_prices", "stock_id, date, high_price, low_price, close_price, volume", dates)
    prices = prices[prices["stock_id"].astype(str).str.match(STOCK_PATTERN)]
    adjust = pd.DataFrame(signals._fetch_all(
        lambda: sb.table("price_adjustments").select("stock_id, date, event_type, factor")
            .gt("date", dates[0]).order("date", desc=False).order("stock_id", desc=False).order("event_type", desc=False)))
    idx = pd.DataFrame(signals._fetch_all(
        lambda: sb.table("market_index").select("date, close").eq("index_code", "TAIEX").order("date", desc=False)))
    print(f"  股價 {len(prices):,} 筆，{time.time() - t:.0f} 秒")
    pv = signals._pivot
    close_raw = pv(prices, "close_price")
    adj = signals.apply_adjustments(prices, adjust)
    close = pv(adj, "close_price").ffill(limit=5)
    mult = (pv(adj, "close_price") / close_raw).ffill()
    high = (pv(prices, "high_price") * mult).reindex_like(close).fillna(close)
    low = (pv(prices, "low_price") * mult).reindex_like(close).fillna(close)
    vol = pv(prices, "volume").reindex_like(close).fillna(0)
    taiex = pd.to_numeric(idx.set_index("date")["close"]).sort_index()
    taiex.index = taiex.index.astype(str)
    close.index = close.index.astype(str)
    for df in (high, low, vol):
        df.index = df.index.astype(str)
    return close, high, low, vol, taiex.reindex(close.index).ffill()


def main():
    from supabase import create_client
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
    close, high, low, vol, taiex = load(sb)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta).clip(lower=0).rolling(14).mean()
    rsi = 100 - 100 / (1 + gain / loss)
    ll, hh = low.rolling(9).min(), high.rolling(9).max()
    rsv = (close - ll) / (hh - ll).replace(0, np.nan) * 100
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    dif = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    hist = dif - dif.ewm(span=9, adjust=False).mean()
    ma20, ma60 = close.rolling(20).mean(), close.rolling(60).mean()
    liquid = vol.rolling(20).mean() >= LIQ
    valid = liquid & ma60.notna()

    entry = close.shift(-1)
    mkt_entry = taiex.shift(-1)
    fwd = {}
    for h in HORIZONS:
        stock = close.shift(-1 - h) / entry - 1
        mkt = taiex.shift(-1 - h) / mkt_entry - 1
        fwd[h] = stock.sub(mkt, axis=0) * 100

    def stat(name, mask):
        mask = mask & valid
        row = {"條件": name, "樣本": int(mask.sum().sum())}
        for h in HORIZONS:
            v = fwd[h].where(mask).stack().dropna()
            row[f"{h}日平均"] = round(v.mean(), 2) if len(v) else None
            row[f"{h}日中位"] = round(v.median(), 2) if len(v) else None
            row[f"{h}日勝率"] = round((v > 0).mean() * 100, 1) if len(v) else None
        return row

    up = close > ma20
    rows = [
        stat("全部（基準）", valid),
        stat("RSI < 30", rsi < 30),
        stat("RSI 30～50", (rsi >= 30) & (rsi < 50)),
        stat("RSI 50～70", (rsi >= 50) & (rsi <= 70)),
        stat("RSI 70～80", (rsi > 70) & (rsi <= 80)),
        stat("RSI > 80", rsi > 80),
        stat("RSI > 70 且站上月線", (rsi > 70) & up),
        stat("RSI 剛升破 70", (rsi > 70) & (rsi.shift(1) <= 70)),
        stat("RSI 從 70 以上跌回", (rsi <= 70) & (rsi.shift(1) > 70)),
        stat("K < 20", k < 20),
        stat("K 20～80", (k >= 20) & (k <= 80)),
        stat("K > 80", k > 80),
        stat("K > 80 連 3 天（鈍化）", (k > 80).rolling(3).sum() == 3),
        stat("KD 黃金交叉", (k > d) & (k.shift(1) <= d.shift(1))),
        stat("KD 黃金交叉（K<30 低檔）", (k > d) & (k.shift(1) <= d.shift(1)) & (k < 30)),
        stat("KD 死亡交叉", (k < d) & (k.shift(1) >= d.shift(1))),
        stat("KD 死亡交叉（K>70 高檔）", (k < d) & (k.shift(1) >= d.shift(1)) & (k > 70)),
        stat("MACD 柱 > 0", hist > 0),
        stat("MACD 柱 < 0", hist < 0),
        stat("MACD 柱 > 0 且變大", (hist > 0) & (hist > hist.shift(1))),
        stat("MACD 柱 > 0 但變小", (hist > 0) & (hist < hist.shift(1))),
        stat("MACD 黃金交叉", (hist > 0) & (hist.shift(1) <= 0)),
        stat("MACD 死亡交叉", (hist < 0) & (hist.shift(1) >= 0)),
    ]
    df = pd.DataFrame(rows).set_index("條件")
    print("\n===== 全期間（相對大盤超額報酬 %，勝率＝跑贏大盤比例）=====")
    print(df.to_string())

    # 分前後兩段，看結論穩不穩
    dates = close.index
    half = dates[len(dates) // 2]
    for label, part in [(f"前半段（～{half}）", dates < half), (f"後半段（{half}～）", dates >= half)]:
        sel = pd.Series(part, index=dates)
        sub = []
        for r_name, m in [("全部（基準）", valid), ("RSI > 70", rsi > 70), ("RSI < 30", rsi < 30),
                          ("K > 80", k > 80), ("K < 20", k < 20),
                          ("KD 黃金交叉", (k > d) & (k.shift(1) <= d.shift(1))),
                          ("KD 死亡交叉", (k < d) & (k.shift(1) >= d.shift(1))),
                          ("MACD 黃金交叉", (hist > 0) & (hist.shift(1) <= 0)),
                          ("MACD 死亡交叉", (hist < 0) & (hist.shift(1) >= 0))]:
            sub.append(stat(r_name, m & pd.DataFrame({c: sel for c in close.columns})))
        print(f"\n===== {label} =====")
        print(pd.DataFrame(sub).set_index("條件")[["樣本", "20日平均", "20日中位", "20日勝率", "5日平均"]].to_string())


if __name__ == "__main__":
    main()
