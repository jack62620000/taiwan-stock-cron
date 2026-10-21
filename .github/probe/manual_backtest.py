"""
暫時的研究腳本（只讀資料庫）：回測「技術指標完整操作手冊」的短線（優化版）、中線、中長線三套規則。
買賣一律「訊號當天收盤確認，隔天收盤成交」，每筆扣 0.585% 交易成本；同一檔股票同時只持有一筆。
用法：python .github/probe/manual_backtest.py
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

COST = 0.585
WARMUP = 130          # 指標暖機的交易日數（季線往前 5 天、DMI、MACD）
LIQ = 500 * 1000      # 20 日均量 ≥ 500 張（股）
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
        lambda: sb.table("market_index").select("date, close").eq("index_code", "TAIEX").order("date", desc=False)))
    print(f"  股價 {len(prices):,} 筆、還原權值 {len(adjust):,} 筆，{time.time() - t:.0f} 秒")
    pv = signals._pivot
    close_raw = pv(prices, "close_price")
    adj = signals.apply_adjustments(prices, adjust)
    close = pv(adj, "close_price").ffill(limit=5)
    mult = (pv(adj, "close_price") / close_raw).ffill()
    high = (pv(prices, "high_price") * mult).reindex_like(close).fillna(close)
    low = (pv(prices, "low_price") * mult).reindex_like(close).fillna(close)
    vol = pv(prices, "volume").reindex_like(close).fillna(0)
    taiex = pd.to_numeric(idx.set_index("date")["close"]).sort_index()
    return close, high, low, vol, taiex


def wilder(x, n):
    return x.ewm(alpha=1 / n, adjust=False).mean()


def indicators(close, high, low, vol):
    ind = {"close": close, "low": low}
    ind["ma5"] = close.rolling(5).mean()
    ind["ma60"] = close.rolling(60).mean()
    ind["ma60_up"] = ind["ma60"] > ind["ma60"].shift(5)
    ind["vol20"] = vol.rolling(20).mean()
    ind["vol"] = vol
    ind["vol5p"] = vol.shift(1).rolling(5).mean()     # 前 5 日均量（不含當天）
    ind["vol10p"] = vol.shift(1).rolling(10).mean()
    ind["vol5"] = vol.rolling(5).mean()
    # KD(9,3,3)：和網站 utils/indicators.calc_kd 相同
    ll, hh = low.rolling(9).min(), high.rolling(9).max()
    rsv = ((close - ll) / (hh - ll).replace(0, np.nan) * 100).clip(0, 100)
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    ind["k"], ind["d"] = k, d
    ind["kd_cross"] = (k > d) & (k.shift(1) <= d.shift(1))
    # RSI(6)，Wilder 平滑（台灣看盤軟體常用）
    delta = close.diff()
    up, dn = wilder(delta.clip(lower=0), 6), wilder((-delta).clip(lower=0), 6)
    ind["rsi6"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    ind["rsi6"] = ind["rsi6"].where(dn > 0, 100)
    # MACD(12,26,9)
    dif = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    ind["dif"], ind["osc"] = dif, dif - dea
    # DMI(14)
    pc = close.shift(1)
    tr = pd.concat([(high - low), (high - pc).abs(), (low - pc).abs()]).groupby(level=0).max().reindex(close.index)
    upm, dnm = high.diff(), -low.diff()
    pdm = upm.where((upm > dnm) & (upm > 0), 0)
    mdm = dnm.where((dnm > upm) & (dnm > 0), 0)
    atr = wilder(tr, 14)
    pdi, mdi = 100 * wilder(pdm, 14) / atr, 100 * wilder(mdm, 14) / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    ind["pdi"], ind["mdi"], ind["adx"] = pdi, mdi, wilder(dx.fillna(0), 14)
    return ind


# ---------- 進場條件（回傳布林寬表）----------
def short_entry(ind, mkt_ok):
    pull = ((ind["k"] < 30) | (ind["rsi6"] < 30)).astype(float).rolling(3).max() > 0
    return (ind["close"] > ind["ma60"]) & ind["ma60_up"] & (ind["vol20"] >= LIQ) & pull \
        & ind["kd_cross"] & (ind["close"] > ind["ma5"]) & (ind["vol"] > ind["vol5p"] * 1.2) \
        & pd.DataFrame({c: mkt_ok for c in ind["close"].columns})


def mid_entry(ind, confirm=True, dmi=True):
    osc = ind["osc"]
    flip = (osc > 0) & (osc.shift(1) <= 0)
    dif_ok = (ind["dif"] > 0) | (ind["dif"] > ind["dif"].shift(3))   # 零軸上，或零軸下往上爬
    cond = flip & dif_ok & (ind["vol20"] >= LIQ)
    if confirm:
        kd_ok = ((ind["k"] > ind["d"]) & (ind["kd_cross"].astype(float).rolling(3).max() > 0)) \
            | ((ind["k"] < 50) & (ind["k"] > ind["k"].shift(1)))
        cond &= kd_ok & (ind["vol"] > ind["vol10p"] * 1.2)
    if dmi:
        cond &= (ind["adx"] > 25) & (ind["pdi"] > ind["mdi"])
    return cond


def long_entry(ind):
    allc = (ind["pdi"] > ind["mdi"]) & (ind["adx"] > 25) & (ind["adx"] > ind["adx"].shift(1)) \
        & (ind["dif"] > 0) & (ind["vol5"] > ind["vol20"]) & (ind["vol20"] >= LIQ)
    return allc & ~allc.shift(1, fill_value=False)


# ---------- 模擬交易 ----------
def simulate(ind, entry, kind, first, half_rule=True):
    """回傳每筆交易：stock_id、訊號日、買進日、賣出日、淨報酬%、持有天數、出場原因"""
    dates = list(ind["close"].index)
    C = ind["close"].to_numpy()
    E = entry.to_numpy()
    arr = {k: ind[k].to_numpy() for k in ("low", "ma5", "rsi6", "osc", "dif", "pdi", "mdi", "adx")}
    trades = []
    n = len(dates)
    for j, sid in enumerate(ind["close"].columns):
        i = first
        while i < n - 2:
            if not E[i, j]:
                i += 1
                continue
            b = i + 1
            P = C[b, j]
            if not P > 0:
                i += 1
                continue
            parts = []       # (權重, 賣出列)
            reason = ""
            hot = False      # RSI 曾 > 80：停損上移到 5 日線
            if kind == "short":
                L = np.nanmin(arr["low"][max(0, i - 5): i + 1, j])
                R = P - L
                if not (R > 0 and 2 * R / P * 100 >= 2):     # 目標（2 倍風險）至少 2%，不然扣成本不划算
                    i += 1
                    continue
                left, t = 1.0, b
                while t < n - 1 and left > 0:
                    c = C[t, j]
                    if c < L:
                        parts.append((left, t + 1)); reason = reason or "停損"; left = 0; break
                    if left == 1.0 and c >= P + 2 * R:
                        parts.append((0.5, t + 1)); left = 0.5; reason = "停利一半"
                    hot = hot or arr["rsi6"][t, j] > 80
                    if left == 0.5 or hot:
                        if c < arr["ma5"][t, j]:
                            parts.append((left, t + 1)); reason = reason or "跌破5日線"; left = 0; break
                    if left == 1.0 and t - b >= 7:
                        parts.append((left, t + 1)); reason = "時間停損"; left = 0; break
                    t += 1
            elif kind == "mid":
                t = b
                while t < n - 1 and not arr["osc"][t, j] < 0:
                    t += 1
                parts.append((1.0, t + 1)); reason = "柱狀翻綠"
            else:  # long
                left, t = 1.0, b
                while t < n - 1:
                    if half_rule and left == 1.0 and arr["adx"][t, j] < arr["adx"][t - 1, j]:
                        parts.append((0.5, t + 1)); left = 0.5
                    if arr["mdi"][t, j] > arr["pdi"][t, j] or arr["dif"][t, j] < 0:
                        parts.append((left, t + 1)); left = 0
                        reason = "−DI 反穿" if arr["mdi"][t, j] > arr["pdi"][t, j] else "DIF 跌破零軸"
                        break
                    t += 1
            if not parts or sum(w for w, _ in parts) < 0.999 or parts[-1][1] >= n:
                break   # 還沒出場（持有中），不算
            ret = sum(w * (C[s, j] / P - 1) for w, s in parts) * 100 - COST
            s_last = parts[-1][1]
            trades.append((sid, dates[i], dates[b], dates[s_last], ret, s_last - b, reason))
            i = s_last          # 出場後才能再進場
    return pd.DataFrame(trades, columns=["stock_id", "signal", "buy", "sell", "ret", "days", "reason"])


def summarize(name, tr, taiex, mkt_ok, mid_date):
    if tr.empty:
        print(f"{name}：沒有交易")
        return
    tx = taiex.reindex(sorted(set(taiex.index) | set(tr["buy"]) | set(tr["sell"]))).ffill()
    tr = tr.assign(mkt=(tx.reindex(tr["sell"]).to_numpy() / tx.reindex(tr["buy"]).to_numpy() - 1) * 100)
    tr["ex"] = tr["ret"] - tr["mkt"]
    tr["above"] = mkt_ok.reindex(tr["signal"]).to_numpy()
    rows = []
    for label, m in [("全部", tr["signal"] >= ""), ("前半", tr["signal"] < mid_date), ("後半", tr["signal"] >= mid_date),
                     ("大盤季線上", tr["above"] == True), ("大盤季線下", tr["above"] == False)]:  # noqa: E712
        d = tr[m]
        if d.empty:
            continue
        rows.append({"期間": label, "筆數": len(d), "平均淨報酬%": d["ret"].mean(), "中位數%": d["ret"].median(),
                     "勝率": (d["ret"] > 0).mean(), "平均持有天": d["days"].mean(), "同期加權%": d["mkt"].mean(),
                     "贏加權%": d["ex"].mean(), "每天淨報酬%": d["ret"].sum() / d["days"].clip(lower=1).sum(),
                     "最差%": d["ret"].min(), "最好%": d["ret"].max()})
    print(f"\n===== {name} =====")
    print(pd.DataFrame(rows).round(2).to_string(index=False))
    print("出場原因：", tr["reason"].value_counts().to_dict())


def main():
    sb = signals._client()
    close, high, low, vol, taiex = load(sb)
    t = time.time()
    ind = indicators(close, high, low, vol)
    tx = taiex.reindex(close.index).ffill()
    mkt_ok = tx > tx.rolling(60).mean()
    first = WARMUP
    dates = list(close.index)
    mid_date = dates[first + (len(dates) - first) // 2]
    print(f"指標 {time.time() - t:.0f} 秒；訊號期間 {dates[first]} ～ {dates[-1]}，前半/後半分界 {mid_date}")
    cases = [
        ("短線（優化版）", short_entry(ind, mkt_ok), "short", {}),
        ("短線（優化版，不看大盤）", short_entry(ind, pd.Series(True, index=close.index)), "short", {}),
        ("中線（手冊全部條件：MACD＋KD＋量＋DMI）", mid_entry(ind, True, True), "mid", {}),
        ("中線（MACD＋KD＋量，不看 DMI）", mid_entry(ind, True, False), "mid", {}),
        ("中線（只看 MACD 翻紅）", mid_entry(ind, False, False), "mid", {}),
        ("中長線（手冊：ADX 一下滑就減半）", long_entry(ind), "long", {"half_rule": True}),
        ("中長線（不做 ADX 減半）", long_entry(ind), "long", {"half_rule": False}),
    ]
    for name, entry, kind, kw in cases:
        t = time.time()
        tr = simulate(ind, entry.fillna(False), kind, first, **kw)
        summarize(name, tr, taiex, mkt_ok, mid_date)
        print(f"（{time.time() - t:.0f} 秒）")


if __name__ == "__main__":
    main()
