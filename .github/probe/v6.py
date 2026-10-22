"""
暫時的研究腳本（只讀資料庫）：v6 綜合分數＝v5 每日評分＋動能、葛蘭碧加減分，
比較 v5 與 v6 各種權重的前 N 名、不同持有天數，用 N 檔等額資金模擬（隔天開盤買賣、單邊成本 0.3%），比較總報酬、最大回撤、勝率。
每日評分用目前規則（v5）在記憶體重算，不寫資料庫；融資融券只有 2025-10-21 起的資料，之前那項沒有分數。
"""
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, HERE)

import signals as S  # noqa: E402
from momentum import STOCK_PATTERN  # noqa: E402
from sim import simulate, summarize  # noqa: E402
from utils.granville import rule_frames  # noqa: E402

START = "2025-01-02"
LIQ = 500 * 1000
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 400)


def main():
    from supabase import create_client
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
    all_dates = S.trading_dates(sb)
    recent = [d for d in all_dates if d >= "2023-11-01"]
    t0 = time.time()
    prices = S.load_daily(sb, "daily_prices", "stock_id, date, open_price, high_price, low_price, close_price, volume", recent)
    prices = prices[prices["stock_id"].astype(str).str.match(STOCK_PATTERN)]
    adjust = pd.DataFrame(S._fetch_all(
        lambda: sb.table("price_adjustments").select("stock_id, date, event_type, factor")
            .gt("date", recent[0]).order("date").order("stock_id").order("event_type")))
    idx = pd.DataFrame(S._fetch_all(lambda: sb.table("market_index").select("date, open, close").eq("index_code", "TAIEX").order("date")))
    ins_dates = [d for d in recent if d >= "2024-11-01"]
    ins = S.load_daily(sb, "stock_ins_daily", "stock_id, date, foreign_net_shares, sitca_net_shares", ins_dates)
    margin = S.load_daily(sb, "margin_utilization", "stock_id, date, margin_balance, short_balance, margin_rate",
                          [d for d in recent if d >= "2025-10-01"])
    holder = pd.DataFrame(S._fetch_all(lambda: sb.table("holder_distribution").select("stock_id, week_date, big_holder_ratio")
                                       .order("week_date").order("stock_id")))
    revenue = pd.DataFrame(S._fetch_all(
        lambda: sb.table("monthly_revenue").select("stock_id, year_month, revenue, yoy_growth, cumulative_yoy_growth")
            .order("year_month").order("stock_id")))
    stocks = pd.DataFrame(S._fetch_all(lambda: sb.table("stock_info").select("stock_id").order("stock_id")))
    print(f"載入完成 {time.time() - t0:.0f} 秒", flush=True)

    # ===== 每日評分（v5）重算 =====
    t0 = time.time()
    sd_list = [d for d in recent if d >= START]
    feat = S.Features({"prices": prices[prices["date"] >= "2024-11-01"][["stock_id", "date", "close_price", "volume"]],
                       "adjust": adjust, "ins": ins, "margin": margin, "holder": holder, "revenue": revenue,
                       "stocks": stocks})
    rank_rows = []
    for d in sd_list:
        for r in S.score_day(feat, d):
            if r["liquid"]:
                rank_rows.append((d, r["stock_id"], r["total_score"], r["chip_score"], r["avg_volume_20"]))
    srows = pd.DataFrame(rank_rows, columns=["date", "stock_id", "total", "chip", "lots"])
    v5_total = srows.pivot(index="date", columns="stock_id", values="total")
    v5_tie = srows.assign(tie=srows["chip"] * 1e-3 + np.log1p(srows["lots"]) * 1e-6).pivot(index="date", columns="stock_id", values="tie")
    print(f"評分重算完成 {time.time() - t0:.0f} 秒", flush=True)

    pv = S._pivot
    adj = S.apply_adjustments(prices, adjust)
    close = pv(adj, "close_price")
    close_ff = close.ffill(limit=5)
    mult = (pv(adj, "close_price") / pv(prices, "close_price")).reindex_like(close).ffill()
    opn = (pv(prices, "open_price").reindex_like(close) * mult).where(lambda x: x > 0)
    high = pv(prices, "high_price").reindex_like(close) * mult
    low = pv(prices, "low_price").reindex_like(close) * mult
    vol = pv(adj, "volume").reindex_like(close).fillna(0)
    dates = list(close.index)
    taiex = pd.to_numeric(idx.set_index("date")["close"]).reindex(dates).ffill()

    c = close_ff
    ma20 = c.rolling(20, min_periods=20).mean()
    ma50 = c.rolling(50, min_periods=50).mean()
    ma150 = c.rolling(150, min_periods=150).mean()
    ma200 = c.rolling(200, min_periods=200).mean()
    high52 = c.rolling(252, min_periods=252).max()
    low52 = c.rolling(252, min_periods=252).min()
    vol20 = vol.rolling(20, min_periods=20).mean()
    vol50 = vol.rolling(50, min_periods=50).mean()
    liq = vol20 >= LIQ
    rs_raw = sum(w * c / c.shift(n) for w, n in ((0.4, 63), (0.2, 126), (0.2, 189), (0.2, 252)))
    rs = (rs_raw.where(liq).rank(axis=1, pct=True) * 100).clip(upper=99).apply(np.floor)
    checks = [(c > ma150) & (c > ma200), ma150 > ma200, ma200 > ma200.shift(20),
              (ma50 > ma150) & (ma50 > ma200), c > ma50, c >= low52 * 1.3, c >= high52 * 0.75, rs >= 70]
    tt = (sum(x.astype(int) for x in checks) == 8) & liq
    ext = (c / ma50 - 1) * 100
    base = tt & (ext < 30)
    vr = vol / vol50.shift(1)
    raw_b = (c > high52.shift(1)) & (vr >= 1.5) & liq
    bo = raw_b & (raw_b.astype(float).shift(1).rolling(10, min_periods=1).max().fillna(0) == 0)
    mid = c.rolling(20, min_periods=20).mean()
    lower = mid - 2 * c.rolling(20, min_periods=20).std(ddof=0)
    touched = (c <= lower).astype(float).shift(1).rolling(10, min_periods=1).max().fillna(0) > 0
    pullback = tt & touched & (c > mid) & (c.shift(1) <= mid.shift(1)) & (ext < 30)
    offh = (c / high52 - 1) * 100
    fr = pv(ins, "foreign_net_shares").reindex_like(c).fillna(0)
    tr = pv(ins, "sitca_net_shares").reindex_like(c).fillna(0)
    f5, t5 = fr.rolling(5).sum(), tr.rolling(5).sum()
    rev = pv(revenue, "revenue", index="year_month")
    yoy = pv(revenue, "yoy_growth", index="year_month").reindex_like(rev)
    ly = rev / (1 + yoy / 100)
    yoy3 = (rev.rolling(3).sum() / ly.rolling(3).sum() - 1) * 100
    avail = pd.Series({ym: S._revenue_available_from(ym) for ym in rev.index})
    last = pd.Series([avail[avail <= dd].index[-1] if (avail <= dd).any() else None for dd in dates], index=dates)
    y3 = yoy3.reindex(last.values).set_axis(dates).reindex(columns=c.columns)

    g = rule_frames(close, ma_days=20, slope_days=5, lookback=5, near_pct=2.0, bias_pct=10.0)["hits"]
    first = lambda h: h & ~h.shift(1, fill_value=False)  # noqa: E731
    g2, g4 = first(g[2]) & liq, first(g[4]) & liq
    gsell = g[5] | g[6] | g[7]

    prev_c = c.shift(1)
    trng = np.maximum(np.maximum(high - low, (high - prev_c).abs()), (low - prev_c).abs())
    atr = trng.rolling(14, min_periods=14).mean()

    mkt = pd.DataFrame({col: (taiex > taiex.rolling(60, min_periods=60).mean()).values for col in c.columns}, index=dates)

    T5 = v5_total.reindex(index=dates, columns=c.columns)
    tie = v5_tie.reindex(index=dates, columns=c.columns).fillna(0)
    rule2_recent = g2.astype(float).rolling(5, min_periods=1).max() > 0   # 近 5 日出現法則 2
    hot = ext >= 30
    width = 4 * c.rolling(20, min_periods=20).std(ddof=0) / mid * 100
    squeeze = base & (width.rolling(120, min_periods=60).rank(pct=True) <= 0.2)
    parts = {"模板": base, "RS90": rs >= 90, "近高": offh >= -5, "回檔": pullback, "新高量增": bo, "布林收斂": squeeze,
             "法則2": rule2_recent, "賣訊": gsell, "漲多": hot}
    P = {k: v.reindex(index=dates, columns=c.columns).fillna(False).astype(float) for k, v in parts.items()}

    def v6(w):
        tot = T5.copy()
        for k, pts in w.items():
            tot = tot + P[k] * pts
        return tot.where(T5.notna())
    weights = {
        "v5（現在）": {},
        "v6a（各加 5、賣訊漲多扣 10）": {"模板": 5, "RS90": 5, "近高": 5, "回檔": 5, "新高量增": 5, "布林收斂": 5, "法則2": 5, "賣訊": -10, "漲多": -10},
        "v6b（各加 10、賣訊漲多扣 20）": {"模板": 10, "RS90": 10, "近高": 10, "回檔": 10, "新高量增": 10, "布林收斂": 10, "法則2": 10, "賣訊": -20, "漲多": -20},
        "v6c（只扣賣訊、漲多各 10）": {"賣訊": -10, "漲多": -10},
        "v6d（動能加 5，不含葛蘭碧）": {"模板": 5, "RS90": 5, "近高": 5, "回檔": 5, "新高量增": 5, "布林收斂": 5, "漲多": -10},
    }
    O, C = opn.values, close.values
    start_i = dates.index(next(d for d in dates if d >= START))
    mid_i = start_i + (len(dates) - start_i) // 2
    print(f"期間 {dates[start_i]} ～ {dates[-1]}，中點 {dates[mid_i]}；大盤在季線上 {mkt.iloc[start_i:, 0].mean() * 100:.0f}% 的天數")
    print(f"加權指數同期：{(taiex.iloc[-1] / taiex.iloc[start_i] - 1) * 100:.1f}%"
          f"（前半 {(taiex.iloc[mid_i] / taiex.iloc[start_i] - 1) * 100:.1f}%、後半 {(taiex.iloc[-1] / taiex.iloc[mid_i] - 1) * 100:.1f}%）", flush=True)
    gsell_v = gsell.reindex(index=dates, columns=c.columns).fillna(False).values
    mkt_v = mkt.values
    allrows = []
    for wname, w in weights.items():
        tot = v6(w) + tie
        rk = tot.rank(axis=1, ascending=False, method="first")
        tot_v = tot.fillna(-1e9).values
        rows = []
        for top in (5, 10, 20):
            sig = ((rk <= top).values & mkt_v)
            for hold in (3, 5, 10, 20, 30, "葛蘭碧賣出"):
                kw = {"hold": hold} if hold != "葛蘭碧賣出" else {}
                trades, eq = simulate(sig, tot_v, O, C, None if kw else gsell_v, top, start_i, **kw)
                row = {"分數": wname, "前N名": top, "持有": hold if hold == "葛蘭碧賣出" else f"{hold} 日"}
                row.update(summarize(trades, eq, dates, start_i, mid_i))
                rows.append(row)
        df = pd.DataFrame(rows)
        allrows.append(df)
        print(f"\n===== {wname}（大盤在季線上才買、前 N 名就用 N 檔等額資金）=====")
        print(df.drop(columns="分數").set_index(["前N名", "持有"]).to_string(), flush=True)
    # 沒有大盤濾網的對照（前 10、持有 20 日）
    print("\n===== 對照：沒有大盤濾網，前 10 名、持有 5／20 日 =====")
    rows = []
    for wname, w in weights.items():
        tot = v6(w) + tie
        rk = tot.rank(axis=1, ascending=False, method="first")
        for hold in (5, 20):
            trades, eq = simulate((rk <= 10).values, tot.fillna(-1e9).values, O, C, None, 10, start_i, hold=hold)
            rows.append({"分數": wname, "持有": f"{hold} 日", **summarize(trades, eq, dates, start_i, mid_i)})
    print(pd.DataFrame(rows).set_index(["分數", "持有"]).to_string())
    df = pd.concat(allrows)
    df["報酬回撤比"] = df["年化%"] / -df["最大回撤%"]
    print("\n===== 年化報酬 ÷ 最大回撤 前 20 名 =====")
    print(df.sort_values("報酬回撤比", ascending=False).head(20).to_string(index=False))
    print("\n===== 各分數在 15 種（前 N 名 × 持有 3～30 日）設定的平均 =====")
    fixed = df[df["持有"] != "葛蘭碧賣出"]
    print(fixed.groupby("分數")[["總報酬%", "最大回撤%", "勝率%", "平均報酬%", "前半%", "後半%"]].mean().round(1).to_string())
    # 今天前 10 名有沒有大量重疊
    for wname, w in list(weights.items())[:2]:
        tot = v6(w) + tie
        print(wname, "最新一天前 10：", list(tot.iloc[-1].nlargest(10).index))

if __name__ == "__main__":
    main()
