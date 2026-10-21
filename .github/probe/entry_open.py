"""
暫時的研究腳本（只讀資料庫）：分析卡各燈號，比較「隔天開盤買」和「隔天收盤買」的差別，並扣交易成本。
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
    prices = signals.load_daily(sb, "daily_prices", "stock_id, date, open_price, high_price, low_price, close_price, volume", dates)
    prices = prices[prices["stock_id"].astype(str).str.match(STOCK_PATTERN)]
    adjust = pd.DataFrame(signals._fetch_all(
        lambda: sb.table("price_adjustments").select("stock_id, date, event_type, factor")
            .gt("date", dates[0]).order("date", desc=False).order("stock_id", desc=False).order("event_type", desc=False)))
    idx = pd.DataFrame(signals._fetch_all(
        lambda: sb.table("market_index").select("date, open, close").eq("index_code", "TAIEX").order("date", desc=False)))
    print(f"  股價 {len(prices):,} 筆，{time.time() - t:.0f} 秒")
    pv = signals._pivot
    close_raw = pv(prices, "close_price")
    adj = signals.apply_adjustments(prices, adjust)
    close = pv(adj, "close_price").ffill(limit=5)
    mult = (pv(adj, "close_price") / close_raw).ffill()
    high = (pv(prices, "high_price") * mult).reindex_like(close).fillna(close)
    low = (pv(prices, "low_price") * mult).reindex_like(close).fillna(close)
    vol = pv(prices, "volume").reindex_like(close).fillna(0)
    opn = (pv(prices, "open_price") * mult).reindex_like(close)
    opn = opn.where(opn > 0)
    taiex = pd.to_numeric(idx.set_index("date")["close"]).sort_index()
    t_open = pd.to_numeric(idx.set_index("date")["open"]).sort_index()
    for x in (taiex, t_open):
        x.index = x.index.astype(str)
    close.index = close.index.astype(str)
    for df in (high, low, vol, opn):
        df.index = df.index.astype(str)
    return close, high, low, vol, opn, taiex.reindex(close.index).ffill(), t_open.reindex(close.index)


COST = 0.585  # 手續費 0.1425% × 2 ＋ 證交稅 0.3%


def main():
    from supabase import create_client
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
    close, high, low, vol, opn, taiex, t_open = load(sb)
    print(f"  有開盤價的比例：{opn.notna().sum().sum() / close.notna().sum().sum() * 100:.1f}%")
    print(f"  大盤有開盤價的天數：{t_open.notna().sum()} / {len(t_open)}")
    ma20 = close.rolling(20).mean()
    sd20 = close.rolling(20).std()
    bb_up, bb_low = ma20 + 2 * sd20, ma20 - 2 * sd20
    bb_pos = (close - bb_low) / (bb_up - bb_low).replace(0, np.nan) * 100
    bias = (close / ma20 - 1) * 100
    hi, lo = high.rolling(240, min_periods=200).max(), low.rolling(240, min_periods=200).min()
    pos = (close - lo) / (hi - lo).replace(0, np.nan) * 100
    vol_ratio = vol / vol.shift(1).rolling(20).mean()
    chg = close.pct_change()
    delta = close.diff()
    rsi = 100 - 100 / (1 + delta.clip(lower=0).rolling(14).mean() / (-delta.clip(upper=0)).rolling(14).mean())
    rsv = (close - low.rolling(9).min()) / (high.rolling(9).max() - low.rolling(9).min()).replace(0, np.nan) * 100
    K = rsv.ewm(com=2, adjust=False).mean()
    D = K.ewm(com=2, adjust=False).mean()
    dif = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    sig = dif.ewm(span=9, adjust=False).mean()
    obv = (np.sign(close.diff()).fillna(0) * vol).cumsum()
    p_hi, p_lo = close >= close.rolling(20).max(), close <= close.rolling(20).min()
    o_hi, o_lo = obv >= obv.rolling(20).max(), obv <= obv.rolling(20).min()
    liquid = vol.rolling(20).mean() >= LIQ
    t_ok = pd.DataFrame({c: t_open.shift(-1).notna() for c in close.columns})
    base = liquid & ma20.notna() & pos.notna() & (close.notna().cumsum() > 60) & opn.shift(-1).notna() & t_ok

    # 進場：隔天收盤 vs 隔天開盤；大盤用同一個時間點
    e_close, m_close = close.shift(-1), taiex.shift(-1)
    e_open, m_open = opn.shift(-1), t_open.shift(-1)
    gap = (e_open / close - 1).sub(m_open / taiex - 1, axis=0) * 100  # 隔天開盤相對訊號日收盤的跳空（扣大盤）

    def fwd(h, entry, m_entry):
        return (close.shift(-1 - h) / entry - 1).sub(taiex.shift(-1 - h) / m_entry - 1, axis=0) * 100

    F = {(h, k): fwd(h, *e) for h in (5, 20) for k, e in (("收", (e_close, m_close)), ("開", (e_open, m_open)))}
    raw20 = (close.shift(-21) / e_open - 1) * 100 - COST  # 實際賺賠：開盤買、持有 20 天、扣成本

    def stat(name, mask):
        mask = mask & base
        row = {"條件": name, "樣本": int(mask.sum().sum())}
        row["跳空"] = round(gap.where(mask).stack().mean(), 2)
        for h in (5, 20):
            for k in ("收", "開"):
                row[f"{h}日{k}"] = round(F[(h, k)].where(mask).stack().dropna().mean(), 2)
        row["差20"] = round(row["20日開"] - row["20日收"], 2)
        row["開扣成本"] = round(row["20日開"] - COST, 2)
        r = raw20.where(mask).stack().dropna()
        row["實際賺賠"] = round(r.mean(), 2)
        row["賺錢機率"] = round((r > 0).mean() * 100, 1)
        return row

    items = [
        ("全部（基準）", base),
        ("RSI ≥ 90 超買", rsi >= 90), ("RSI 70～90 強勢", (rsi >= 70) & (rsi < 90)),
        ("RSI 50～70 偏強", (rsi >= 50) & (rsi < 70)), ("RSI 30～50 偏弱", (rsi >= 30) & (rsi < 50)), ("RSI < 30 超賣", rsi < 30),
        ("K ≥ 90 超買", K >= 90), ("K 80～90 強勢", (K >= 80) & (K < 90)),
        ("K 20～50 偏弱", (K >= 20) & (K < 50)), ("K < 20 超賣", K < 20),
        ("位階 ≥ 90% 接近年高", pos >= 90), ("位階 70～90% 偏高", (pos >= 70) & (pos < 90)),
        ("位階 10～50% 中下段", (pos >= 10) & (pos < 50)), ("位階 < 10% 接近年低", pos < 10),
        ("乖離 ≥ +10%", bias >= 10), ("乖離 −10～0% 跌破月線", (bias >= -10) & (bias < 0)), ("乖離 < −10%", bias < -10),
        ("布林突破上軌", bb_pos >= 100), ("布林靠近上軌", (bb_pos >= 80) & (bb_pos < 100)),
        ("布林靠近下軌", (bb_pos >= 0) & (bb_pos < 20)), ("布林跌破下軌", bb_pos < 0),
        ("價漲量增（量比 ≥ 1.5）", (vol_ratio >= 1.5) & (chg > 0)), ("爆量 ≥ 3 倍", vol_ratio >= 3),
        ("MACD 零軸上", (dif > 0) & (sig > 0)), ("MACD 零軸下", (dif < 0) & (sig < 0)),
        ("OBV 量價齊揚", p_hi & o_hi), ("OBV 先破底", o_lo & ~p_lo),
        ("KD 黃金交叉", (K > D) & (K.shift(1) <= D.shift(1))),
        ("KD 死亡交叉 K ≥ 80", (K < D) & (K.shift(1) >= D.shift(1)) & (K >= 80)),
    ]
    rows = [stat(n, m) for n, m in items]
    print("\n===== 隔天收盤買 vs 隔天開盤買（相對大盤超額報酬 %）=====")
    print("跳空＝隔天開盤比訊號當天收盤高幾 %（扣大盤）；差20＝開盤買減收盤買的 20 日超額報酬；")
    print("開扣成本＝開盤買 20 日超額報酬再扣 0.585% 成本；實際賺賠＝開盤買、持有 20 天、扣成本、不和大盤比")
    print(pd.DataFrame(rows).set_index("條件").to_string())

if __name__ == "__main__":
    main()
