"""
暫時的研究腳本（只讀資料庫）：綜合葛蘭碧、每日選股、動能選股的買進訊號，搭配不同賣出規則，
用 10 檔等額資金模擬（隔天開盤買賣、單邊成本 0.3%），比較總報酬、最大回撤、勝率。
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
            if r["rank"] is not None:
                rank_rows.append((d, r["stock_id"], r["rank"]))
    score_rank = pd.DataFrame(rank_rows, columns=["date", "stock_id", "rank"]).pivot(index="date", columns="stock_id", values="rank")
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

    sr = score_rank.reindex(index=dates, columns=c.columns)
    mkt = pd.DataFrame({col: (taiex > taiex.rolling(60, min_periods=60).mean()).values for col in c.columns}, index=dates)

    elite = base & (rs >= 90) & (offh >= -5) & (y3 >= 20)
    chip = base & (rs >= 90) & (f5 > 0) & (t5 > 0)
    entries = {
        "A 趨勢模板（基準）": (base, rs),
        "B 動能精選（RS≥90＋離高5%內＋營收年增≥20%）": (elite, rs),
        "C 動能＋法人（RS≥90＋外資投信都買超）": (chip, rs),
        "D 趨勢中回檔再起": (pullback, rs),
        "E 兩者同時（模板＋新高量增）": (base & bo, rs),
        "F 葛蘭碧法則 2": (g2, rs),
        "G 葛蘭碧法則 4": (g4, -rs),
        "H 每日評分前 10": (sr <= 10, -sr),
        "I 每日評分前 50＋動能精選": ((sr <= 50) & elite, rs),
        "J 精選或法人或回檔再起": (elite | chip | pullback, rs),
    }
    exits = {
        "持有 20 日": {"hold": 20},
        "持有 60 日": {"hold": 60},
        "跌破 20 日線": {"ex": c < ma20},
        "跌破 50 日線": {"ex": c < ma50},
        "ATR 停損＋移動停利": {"init_stop": True},
        "葛蘭碧賣出（法則 5～7）": {"ex": gsell},
    }

    O, C = opn.values, close.values
    start_i = dates.index(next(d for d in dates if d >= START))
    mid_i = start_i + (len(dates) - start_i) // 2
    atr_v = atr.values
    print(f"期間 {dates[start_i]} ～ {dates[-1]}，中點 {dates[mid_i]}；大盤在季線上 {mkt.iloc[start_i:, 0].mean() * 100:.0f}% 的天數")
    print(f"加權指數同期：{(taiex.iloc[-1] / taiex.iloc[start_i] - 1) * 100:.1f}%"
          f"（前半 {(taiex.iloc[mid_i] / taiex.iloc[start_i] - 1) * 100:.1f}%、後半 {(taiex.iloc[-1] / taiex.iloc[mid_i] - 1) * 100:.1f}%）")
    tq = taiex.iloc[start_i:]
    print(f"加權指數最大回撤：{(tq / tq.cummax() - 1).min() * 100:.1f}%", flush=True)

    best = []
    for ename, (sig, rk) in entries.items():
        rk_v = rk.reindex(index=dates, columns=c.columns).fillna(-1e9).values
        rows = []
        for filt in (False, True):
            s = sig.reindex(index=dates, columns=c.columns).fillna(False)
            if filt:
                s = s & mkt
            s_v = s.values.astype(bool)
            for xname, xr in exits.items():
                kw = {}
                ex_v = None
                if "hold" in xr:
                    kw["hold"] = xr["hold"]
                elif "init_stop" in xr:
                    kw["init_stop"] = (atr_v, 3.0, 0.08)
                else:
                    ex_v = xr["ex"].reindex(index=dates, columns=c.columns).fillna(False).values
                trades, eq = simulate(s_v, rk_v, O, C, ex_v, 10, start_i, **kw)
                row = {"賣出": xname, "大盤濾網": "有" if filt else "無"}
                row.update(summarize(trades, eq, dates, start_i, mid_i))
                rows.append(row)
                best.append({"買進": ename, **row})
        print(f"\n===== {ename}：訊號平均每天 {sig.reindex(index=dates[start_i:]).sum(axis=1).mean():.1f} 檔（10 檔等額資金）=====")
        print(pd.DataFrame(rows).set_index(["大盤濾網", "賣出"]).to_string(), flush=True)

    df = pd.DataFrame(best)
    df = df[df["交易數"] >= 30]
    print("\n===== 總報酬前 15 名 =====")
    print(df.sort_values("總報酬%", ascending=False).head(15).to_string(index=False))
    df["報酬回撤比"] = df["年化%"] / -df["最大回撤%"]
    print("\n===== 年化報酬 ÷ 最大回撤 前 15 名 =====")
    print(df.sort_values("報酬回撤比", ascending=False).head(15).to_string(index=False))


if __name__ == "__main__":
    main()
