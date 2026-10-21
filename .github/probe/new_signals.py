"""
暫時的研究腳本（只讀資料庫）：KD 分區交叉、MACD 零軸與柱狀、布林通道收窄、OBV 量價背離之後的股價表現。
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

    # ===== KD(9,3,3) 分區交叉 =====
    rsv = (close - low.rolling(9).min()) / (high.rolling(9).max() - low.rolling(9).min()).replace(0, np.nan) * 100
    K = rsv.ewm(com=2, adjust=False).mean()
    D = K.ewm(com=2, adjust=False).mean()
    gold = (K > D) & (K.shift(1) <= D.shift(1))
    dead = (K < D) & (K.shift(1) >= D.shift(1))
    show("KD 黃金交叉（依交叉當天 K 值）", [
        ("黃金交叉（全部）", gold), ("K < 20", gold & (K < 20)), ("K 20～50", gold & (K >= 20) & (K < 50)),
        ("K 50～80", gold & (K >= 50) & (K < 80)), ("K ≥ 80", gold & (K >= 80)),
    ], valid)
    show("KD 死亡交叉（依交叉當天 K 值）", [
        ("死亡交叉（全部）", dead), ("K ≥ 80", dead & (K >= 80)), ("K 50～80", dead & (K >= 50) & (K < 80)),
        ("K 20～50", dead & (K >= 20) & (K < 50)), ("K < 20", dead & (K < 20)),
    ], valid)

    # ===== MACD(12,26,9) 零軸與柱狀 =====
    dif = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    sig = dif.ewm(span=9, adjust=False).mean()
    hist = dif - sig
    up3 = (hist > hist.shift(1)) & (hist.shift(1) > hist.shift(2)) & (hist.shift(2) > hist.shift(3))
    dn3 = (hist < hist.shift(1)) & (hist.shift(1) < hist.shift(2)) & (hist.shift(2) < hist.shift(3))
    mgold = (hist > 0) & (hist.shift(1) <= 0)
    mdead = (hist < 0) & (hist.shift(1) >= 0)
    valid_m = valid & dif.notna() & close.notna() & (close.notna().cumsum() > 60)
    show("MACD 零軸", [
        ("DIF、訊號線都 > 0（多頭）", (dif > 0) & (sig > 0)), ("DIF、訊號線都 < 0（空頭）", (dif < 0) & (sig < 0)),
        ("一正一負", ((dif > 0) & (sig <= 0)) | ((dif <= 0) & (sig > 0))),
    ], valid_m)
    show("MACD 柱狀變化（連續 3 天）", [
        ("柱 > 0 且連 3 天放大", (hist > 0) & up3), ("柱 > 0 且連 3 天縮小", (hist > 0) & dn3),
        ("柱 < 0 且連 3 天放大（更負）", (hist < 0) & dn3), ("柱 < 0 且連 3 天縮小（收斂）", (hist < 0) & up3),
    ], valid_m)
    show("MACD 交叉位置", [
        ("黃金交叉在零軸上", mgold & (dif > 0)), ("黃金交叉在零軸下", mgold & (dif <= 0)),
        ("死亡交叉在零軸上", mdead & (dif > 0)), ("死亡交叉在零軸下", mdead & (dif <= 0)),
    ], valid_m)

    # ===== 布林通道收窄（帶寬在近 120 日最低的 10%）=====
    width = (bb_up - bb_low) / ma20.replace(0, np.nan) * 100
    wrank = width.rolling(120, min_periods=100).rank(pct=True)
    squeeze = wrank <= 0.10
    sq_recent = squeeze.shift(1).rolling(5, min_periods=1).max().fillna(0).astype(bool)
    show("布林通道收窄", [
        ("收窄中（帶寬近 120 日最低 10%）", squeeze),
        ("收窄後 5 日內突破上軌", sq_recent & (close > bb_up)), ("收窄後 5 日內跌破下軌", sq_recent & (close < bb_low)),
        ("一般時候突破上軌", ~sq_recent & (close > bb_up)), ("一般時候跌破下軌", ~sq_recent & (close < bb_low)),
    ], valid & wrank.notna())

    # ===== OBV 量價背離 =====
    obv = (np.sign(close.diff()).fillna(0) * vol).cumsum()
    for n in (20, 60):
        p_hi = close >= close.rolling(n).max()
        o_hi = obv >= obv.rolling(n).max()
        p_lo = close <= close.rolling(n).min()
        o_lo = obv <= obv.rolling(n).min()
        show(f"OBV（{n} 日新高／新低）", [
            ("量價齊揚：股價、OBV 都創新高", p_hi & o_hi), ("量先價行：OBV 創新高、股價沒有", o_hi & ~p_hi),
            ("頂背離：股價創新高、OBV 沒有", p_hi & ~o_hi),
            ("量價齊跌：股價、OBV 都創新低", p_lo & o_lo), ("底背離：股價創新低、OBV 沒有", p_lo & ~o_lo),
            ("OBV 先破底、股價沒有", o_lo & ~p_lo),
        ], valid & (close.notna().cumsum() > n))

if __name__ == "__main__":
    main()
