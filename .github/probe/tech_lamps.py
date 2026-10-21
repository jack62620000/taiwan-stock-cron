"""
暫時的研究腳本（只讀資料庫）：股價位階、布林通道、乖離率、量能各區間之後的股價表現。
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
    ma20 = close.rolling(20).mean()
    sd20 = close.rolling(20).std()
    bb_up, bb_low = ma20 + 2 * sd20, ma20 - 2 * sd20
    bb_pos = (close - bb_low) / (bb_up - bb_low).replace(0, np.nan) * 100
    bias = (close / ma20 - 1) * 100
    hi, lo = high.rolling(240, min_periods=200).max(), low.rolling(240, min_periods=200).min()
    pos = (close - lo) / (hi - lo).replace(0, np.nan) * 100
    new_high = close >= close.rolling(240, min_periods=200).max()
    vol_ratio = vol / vol.shift(1).rolling(20).mean()
    chg = close.pct_change()
    liquid = vol.rolling(20).mean() >= LIQ
    valid = liquid & ma20.notna()
    valid_pos = valid & pos.notna()

    entry = close.shift(-1)
    mkt_entry = taiex.shift(-1)
    fwd = {}
    for h in HORIZONS:
        fwd[h] = (close.shift(-1 - h) / entry - 1).sub(taiex.shift(-1 - h) / mkt_entry - 1, axis=0) * 100

    dates = close.index
    half = dates[len(dates) // 2]
    first = pd.DataFrame({c: pd.Series(dates < half, index=dates) for c in close.columns})

    def stat(name, mask, base):
        mask = mask & base
        row = {"條件": name, "樣本": int(mask.sum().sum())}
        for h in HORIZONS:
            v = fwd[h].where(mask).stack().dropna()
            row[f"{h}日平均"] = round(v.mean(), 2) if len(v) else None
            row[f"{h}日勝率"] = round((v > 0).mean() * 100, 1) if len(v) else None
        for lbl, part in [("前半20日", first), ("後半20日", ~first)]:
            v = fwd[20].where(mask & part).stack().dropna()
            row[lbl] = round(v.mean(), 2) if len(v) else None
        return row

    def show(title, items, base):
        rows = [stat("全部（基準）", base, base)] + [stat(n, m, base) for n, m in items]
        print(f"\n===== {title}（相對大盤超額報酬 %）=====")
        print(pd.DataFrame(rows).set_index("條件").to_string())

    show("股價位階（52 週區間位置）", [
        ("< 10%", pos < 10), ("10～30%", (pos >= 10) & (pos < 30)), ("30～50%", (pos >= 30) & (pos < 50)),
        ("50～70%", (pos >= 50) & (pos < 70)), ("70～90%", (pos >= 70) & (pos < 90)), ("≥ 90%", pos >= 90),
        ("創 52 週新高", new_high),
    ], valid_pos)
    show("布林通道位置", [
        ("跌破下軌 < 0%", bb_pos < 0), ("靠近下軌 0～20%", (bb_pos >= 0) & (bb_pos <= 20)),
        ("中段 20～80%", (bb_pos > 20) & (bb_pos < 80)), ("靠近上軌 80～100%", (bb_pos >= 80) & (bb_pos <= 100)),
        ("突破上軌 > 100%", bb_pos > 100),
    ], valid)
    show("20 日乖離率", [
        ("< -10%", bias < -10), ("-10～-5%", (bias >= -10) & (bias < -5)), ("-5～0%", (bias >= -5) & (bias < 0)),
        ("0～5%", (bias >= 0) & (bias < 5)), ("5～10%", (bias >= 5) & (bias <= 10)),
        ("10～20%", (bias > 10) & (bias <= 20)), ("> 20%", bias > 20),
    ], valid)
    show("量能（量比＝當日量 ÷ 前 20 日均量）", [
        ("量縮 < 0.7", vol_ratio < 0.7), ("正常 0.7～1.5", (vol_ratio >= 0.7) & (vol_ratio < 1.5)),
        ("放量 ≥ 1.5 且上漲", (vol_ratio >= 1.5) & (chg > 0)), ("放量 ≥ 1.5 且下跌", (vol_ratio >= 1.5) & (chg < 0)),
        ("爆量 ≥ 3 且上漲", (vol_ratio >= 3) & (chg > 0)), ("爆量 ≥ 3 且下跌", (vol_ratio >= 3) & (chg < 0)),
    ], valid)


if __name__ == "__main__":
    main()
