"""
暫時的研究腳本（只讀資料庫）：動能選股加條件能不能更好（RS、離高點、籌碼、營收／EPS、波動收斂、大盤濾網）。
隔天開盤買，看 20／60 個交易日相對大盤（大盤也用隔天開盤）的超額報酬，再減全部股票平均。
"""
import os
import sys
import time

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import signals as S  # noqa: E402
from momentum import STOCK_PATTERN  # noqa: E402

START = "2025-01-02"
LIQ = 500 * 1000
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 300)


def main():
    from supabase import create_client
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
    all_dates = S.trading_dates(sb)
    recent = [d for d in all_dates if d >= "2023-11-01"]
    t = time.time()
    prices = S.load_daily(sb, "daily_prices", "stock_id, date, open_price, high_price, low_price, close_price, volume", recent)
    prices = prices[prices["stock_id"].astype(str).str.match(STOCK_PATTERN)]
    adjust = pd.DataFrame(S._fetch_all(
        lambda: sb.table("price_adjustments").select("stock_id, date, event_type, factor")
            .gt("date", recent[0]).order("date").order("stock_id").order("event_type")))
    idx = pd.DataFrame(S._fetch_all(lambda: sb.table("market_index").select("date, open, close").eq("index_code", "TAIEX").order("date")))
    ins = S.load_daily(sb, "stock_ins_daily", "stock_id, date, foreign_net_shares, sitca_net_shares",
                       [d for d in recent if d >= "2024-11-01"])
    revenue = pd.DataFrame(S._fetch_all(
        lambda: sb.table("monthly_revenue").select("stock_id, year_month, revenue, yoy_growth")
            .order("year_month").order("stock_id")))
    qr = pd.DataFrame(S._fetch_all(
        lambda: sb.table("quarterly_report").select("stock_id, year, quarter, eps")
            .order("year").order("quarter").order("stock_id")))
    print(f"載入完成 {time.time() - t:.0f} 秒")

    pv = S._pivot
    adj = S.apply_adjustments(prices, adjust)
    close = pv(adj, "close_price").ffill(limit=5)
    mult = (pv(adj, "close_price") / pv(prices, "close_price")).reindex_like(close).ffill()
    opn = (pv(prices, "open_price").reindex_like(close) * mult).where(lambda x: x > 0)
    high = pv(prices, "high_price").reindex_like(close) * mult
    low = pv(prices, "low_price").reindex_like(close) * mult
    vol = pv(adj, "volume").reindex_like(close).fillna(0)
    dates = list(close.index)
    taiex = pd.to_numeric(idx.set_index("date")["close"]).reindex(dates).ffill()
    t_open = pd.to_numeric(idx.set_index("date")["open"]).reindex(dates)

    ma50 = close.rolling(50, min_periods=50).mean()
    ma150 = close.rolling(150, min_periods=150).mean()
    ma200 = close.rolling(200, min_periods=200).mean()
    high52 = close.rolling(252, min_periods=252).max()
    low52 = close.rolling(252, min_periods=252).min()
    vol20 = vol.rolling(20, min_periods=20).mean()
    vol50 = vol.rolling(50, min_periods=50).mean()
    liq_all = vol20 >= LIQ
    rs_raw = sum(w * close / close.shift(n) for w, n in ((0.4, 63), (0.2, 126), (0.2, 189), (0.2, 252)))
    rs = (rs_raw.where(liq_all).rank(axis=1, pct=True) * 100).clip(upper=99).apply(np.floor)
    checks = [(close > ma150) & (close > ma200), ma150 > ma200, ma200 > ma200.shift(20),
              (ma50 > ma150) & (ma50 > ma200), close > ma50, close >= low52 * 1.3, close >= high52 * 0.75, rs >= 70]
    tt = (sum(c.astype(int) for c in checks) == 8) & liq_all
    ext = (close / ma50 - 1) * 100
    base = tt & (ext < 30)
    vr = vol / vol50.shift(1)
    raw_b = (close > high52.shift(1)) & (vr >= 1.5) & liq_all
    bo = raw_b & (raw_b.astype(float).shift(1).rolling(10, min_periods=1).max().fillna(0) == 0)
    mid = close.rolling(20, min_periods=20).mean()
    sdv = close.rolling(20, min_periods=20).std(ddof=0)
    lower = mid - 2 * sdv
    touched = (close <= lower).astype(float).shift(1).rolling(10, min_periods=1).max().fillna(0) > 0
    pullback = tt & touched & (close > mid) & (close.shift(1) <= mid.shift(1))
    offh = (close / high52 - 1) * 100

    def fwd(h):
        return (close.shift(-1 - h) / opn.shift(-1) - 1).sub(taiex.shift(-1 - h) / t_open.shift(-1) - 1, axis=0) * 100
    sd = pd.Index([d for d in dates if d >= START])
    W = lambda df: df.reindex(index=sd, columns=close.columns)  # noqa: E731
    F20, F60 = W(fwd(20)), W(fwd(60))
    liquid = W(liq_all & opn.shift(-1).notna())
    half = sd[len(sd) // 2]
    first = pd.DataFrame({c: sd < half for c in close.columns}, index=sd)
    base20 = F20.where(liquid).stack().mean()
    base60 = F60.where(liquid).stack().mean()
    b60a = F60.where(liquid & first).stack().mean()
    b60b = F60.where(liquid & ~first).stack().mean()
    ndays = len(sd)

    def stat(name, m):
        m = W(m.astype(float)).fillna(0).astype(bool) & liquid
        v20, v60 = F20.where(m).stack().dropna(), F60.where(m).stack().dropna()
        return {"條件": name, "樣本": len(v20), "每天幾檔": round(len(v20) / ndays, 1),
                "20日比平均": round(v20.mean() - base20, 2), "60日比平均": round(v60.mean() - base60, 2),
                "60日勝率": round((v60 > 0).mean() * 100, 1),
                "60日中位數": round(v60.median() - base60, 2),
                "前半60": round(F60.where(m & first).stack().mean() - b60a, 2),
                "後半60": round(F60.where(m & ~first).stack().mean() - b60b, 2)}

    def show(title, items):
        print(f"\n===== {title}（比平均＝減全部股票平均；全部平均 20日 {base20:.2f}%、60日 {base60:.2f}%）=====")
        print(pd.DataFrame([stat(n, m) for n, m in items]).set_index("條件").to_string())

    B = "基準（趨勢模板、離 50 日線 < 30%）"
    show("對照", [(B, base), ("趨勢中回檔再起", pullback & (ext < 30)), ("兩者同時", base & bo), ("52 週新高＋量增", bo & (ext < 30))])

    # 1. RS
    show("1. RS 門檻", [("RS 70～80", base & (rs < 80)), ("RS ≥ 80", base & (rs >= 80)), ("RS ≥ 90", base & (rs >= 90)),
                        ("RS ≥ 95", base & (rs >= 95))])
    # 2. 離 52 週高點
    show("2. 離 52 週高點", [("新高（0）", base & (offh >= 0)), ("5% 以內", base & (offh >= -5)), ("10% 以內", base & (offh >= -10)),
                          ("10～25%", base & (offh < -10)), ("5% 以內＋RS ≥ 90", base & (offh >= -5) & (rs >= 90))])

    # 3. 籌碼、營收、EPS
    def streak(s):
        pos = s > 0
        return pos.cumsum() - pos.cumsum().where(~pos).ffill().fillna(0)
    fr = pv(ins, "foreign_net_shares").reindex_like(close).fillna(0)
    tr = pv(ins, "sitca_net_shares").reindex_like(close).fillna(0)
    f5, t5 = fr.rolling(5).sum(), tr.rolling(5).sum()
    show("3a. 籌碼", [("外資近 5 日買超", base & (f5 > 0)), ("外資連買 ≥ 3 天", base & (streak(fr) >= 3)),
                     ("投信近 5 日買超", base & (t5 > 0)), ("投信連買 ≥ 3 天", base & (streak(tr) >= 3)),
                     ("外資、投信近 5 日都買超", base & (f5 > 0) & (t5 > 0)), ("外資近 5 日賣超", base & (f5 < 0))])
    rev = pv(revenue, "revenue", index="year_month")
    yoy = pv(revenue, "yoy_growth", index="year_month").reindex_like(rev)
    ly = rev / (1 + yoy / 100)
    yoy3 = (rev.rolling(3).sum() / ly.rolling(3).sum() - 1) * 100
    avail = pd.Series({ym: S._revenue_available_from(ym) for ym in rev.index})

    def daily(monthly):
        last = pd.Series([avail[avail <= dd].index[-1] if (avail <= dd).any() else None for dd in sd], index=sd)
        return monthly.reindex(last.values).set_axis(sd).reindex(columns=close.columns)
    yoy_d, y3_d = daily(yoy), daily(yoy3)
    q = qr.copy()
    q["eps"] = pd.to_numeric(q["eps"], errors="coerce")
    q["year"], q["quarter"] = q["year"].astype(int), q["quarter"].astype(int)
    av = {1: "05-16", 2: "08-15", 3: "11-15"}
    q["avail"] = [f"{y + 1}-04-01" if qq == 4 else f"{y}-{av[qq]}" for y, qq in zip(q["year"], q["quarter"])]
    key = q.set_index(["stock_id", "year", "quarter"])
    prev = key["eps"].reindex(list(zip(q["stock_id"], q["year"] - 1, q["quarter"]))).values
    q["eps_yoy"] = np.where(np.abs(prev) > 0, (q["eps"] - prev) / np.abs(prev) * 100, np.nan)
    w = q.sort_values(["avail", "year", "quarter"]).pivot_table(index="avail", columns="stock_id", values="eps_yoy", aggfunc="last")
    eps_yoy = w.reindex(sorted(set(w.index) | set(dates))).ffill().reindex(sd).reindex(columns=close.columns)
    show("3b. 營收、EPS", [("月營收年增 ≥ 20%", base & (yoy_d >= 20)), ("月營收年增 ≥ 50%", base & (yoy_d >= 50)),
                         ("月營收年增 < 0", base & (yoy_d < 0)), ("近 3 月營收年增 ≥ 20%", base & (y3_d >= 20)),
                         ("EPS 年增 ≥ 50%", base & (eps_yoy >= 50)), ("EPS 年增 < 0", base & (eps_yoy < 0)),
                         ("近 3 月營收年增 ≥ 20%＋EPS 年增 ≥ 50%", base & (y3_d >= 20) & (eps_yoy >= 50))])

    # 4. 波動收斂（VCP）
    width = 4 * sdv / mid * 100
    w_rank = width.rolling(120, min_periods=60).rank(pct=True)
    prev_c = close.shift(1)
    tr_ = np.maximum(np.maximum(high - low, (high - prev_c).abs()), (low - prev_c).abs())
    atrp = tr_.rolling(14, min_periods=14).mean() / close * 100
    hi20p = close.rolling(20, min_periods=20).max().shift(1)
    lo20p = close.rolling(20, min_periods=20).min().shift(1)
    rng20 = (hi20p / lo20p - 1) * 100
    dry = vol.rolling(10).mean().shift(1) < vol50.shift(1) * 0.8
    squeeze = w_rank.shift(1) <= 0.2
    show("4. 波動收斂", [
        ("布林寬度在近 120 日最窄 20%", base & (w_rank <= 0.2)), ("ATR% 比 20 日前少 20% 以上", base & (atrp < atrp.shift(20) * 0.8)),
        ("前 10 日量縮（< 50 日均量 8 成）", base & dry),
        ("收斂後突破：前一天布林最窄 20%＋突破前 20 日高＋量比 ≥ 1.5", base & squeeze & (close > hi20p) & (vr >= 1.5)),
        ("前 20 日高低差 < 15%＋突破前 20 日高＋量比 ≥ 1.5", base & (rng20 < 15) & (close > hi20p) & (vr >= 1.5)),
        ("同上＋前 10 日量縮", base & (rng20 < 15) & (close > hi20p) & (vr >= 1.5) & dry)])

    # 5. 大盤濾網
    tma60 = taiex.rolling(60, min_periods=60).mean()
    tma20 = taiex.rolling(20, min_periods=20).mean()
    up60 = pd.DataFrame({c: (taiex > tma60).values for c in close.columns}, index=close.index)
    up20 = pd.DataFrame({c: (taiex > tma20).values for c in close.columns}, index=close.index)
    show("5. 大盤濾網", [("大盤在季線上", base & up60), ("大盤在季線下", base & ~up60),
                       ("大盤在月線上", base & up20), ("大盤在月線下", base & ~up20),
                       ("回檔再起＋大盤在季線上", pullback & (ext < 30) & up60), ("回檔再起＋大盤在季線下", pullback & (ext < 30) & ~up60)])
    print(f"大盤在季線上的天數比例：{up60.iloc[:, 0].reindex(sd).mean() * 100:.0f}%")

    # 組合
    show("組合", [
        ("RS ≥ 90＋離高點 5% 以內", base & (rs >= 90) & (offh >= -5)),
        ("RS ≥ 90＋近 3 月營收年增 ≥ 20%", base & (rs >= 90) & (y3_d >= 20)),
        ("RS ≥ 90＋離高點 5% 內＋近 3 月營收年增 ≥ 20%", base & (rs >= 90) & (offh >= -5) & (y3_d >= 20)),
        ("RS ≥ 90＋外資、投信近 5 日都買超", base & (rs >= 90) & (f5 > 0) & (t5 > 0)),
        ("RS ≥ 90＋大盤在季線上", base & (rs >= 90) & up60),
        ("RS ≥ 80＋近 3 月營收年增 ≥ 20%＋大盤在季線上", base & (rs >= 80) & (y3_d >= 20) & up60),
        ("回檔再起＋RS ≥ 80", pullback & (ext < 30) & (rs >= 80)),
        ("回檔再起＋近 3 月營收年增 ≥ 20%", pullback & (ext < 30) & (y3_d >= 20)),
    ])


if __name__ == "__main__":
    main()
