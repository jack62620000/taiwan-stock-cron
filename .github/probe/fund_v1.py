"""
暫時的研究腳本（只讀資料庫）：分析卡基本面（EPS、ROE、本益比、P/B、現金流）的等級，
用「隔天開盤買」看之後 10／20 個交易日相對大盤（大盤也用隔天開盤）的超額報酬。
只算 20 日均量 ≥ 500 張的股票，期間 2025-02 起（法人、融資、營收資料齊全）。
"""
import os
import sys
import time

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "app"))

import signals as S  # noqa: E402
from momentum import STOCK_PATTERN  # noqa: E402

START = "2025-02-03"
LIQ = 500 * 1000
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 300)


def main():
    from supabase import create_client
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
    # utils.granville 不連資料庫，可以直接 import

    all_dates = S.trading_dates(sb)
    recent = [d for d in all_dates if d >= "2024-12-01"]
    t = time.time()
    prices = S.load_daily(sb, "daily_prices", "stock_id, date, open_price, close_price, volume", recent)
    prices = prices[prices["stock_id"].astype(str).str.match(STOCK_PATTERN)]
    adjust = pd.DataFrame(S._fetch_all(
        lambda: sb.table("price_adjustments").select("stock_id, date, event_type, factor")
            .gt("date", recent[0]).order("date").order("stock_id").order("event_type")))
    qr = pd.DataFrame(S._fetch_all(
        lambda: sb.table("quarterly_report").select("stock_id, year, quarter, eps, roe, book_value, operating_cf, free_cf")
            .order("year").order("quarter").order("stock_id")))
    idx = pd.DataFrame(S._fetch_all(lambda: sb.table("market_index").select("date, open, close").eq("index_code", "TAIEX").order("date")))
    print(f"載入完成 {time.time() - t:.0f} 秒")

    pv = S._pivot
    adj = S.apply_adjustments(prices, adjust)
    close = pv(adj, "close_price").ffill(limit=5)
    mult = (pv(adj, "close_price") / pv(prices, "close_price")).reindex_like(close).ffill()
    opn = (pv(prices, "open_price").reindex_like(close) * mult).where(lambda x: x > 0)
    vol = pv(adj, "volume").reindex_like(close).fillna(0)
    dates = list(close.index)
    taiex = pd.to_numeric(idx.set_index("date")["close"]).reindex(dates).ffill()
    t_open = pd.to_numeric(idx.set_index("date")["open"]).reindex(dates)

    def fwd(h):
        return (close.shift(-1 - h) / opn.shift(-1) - 1).sub(taiex.shift(-1 - h) / t_open.shift(-1) - 1, axis=0) * 100
    F10, F20 = fwd(10), fwd(20)

    sd = pd.Index([d for d in dates if d >= START])
    W = lambda df: df.reindex(index=sd, columns=close.columns)  # noqa: E731
    liquid = W((vol.rolling(20).mean() >= LIQ) & opn.shift(-1).notna())
    liquid = liquid & pd.DataFrame({c: t_open.shift(-1).reindex(sd).notna().values for c in close.columns}, index=sd)
    F10, F20 = W(F10), W(F20)
    half = sd[len(sd) // 2]
    first = pd.DataFrame({c: sd < half for c in close.columns}, index=sd)
    base20 = F20.where(liquid).stack().mean()
    base10 = F10.where(liquid).stack().mean()

    def stat(name, m):
        m = W(m.astype(float)).fillna(0).astype(bool) & liquid
        v20, v10 = F20.where(m).stack().dropna(), F10.where(m).stack().dropna()
        a = F20.where(m & first).stack().dropna()
        b = F20.where(m & ~first).stack().dropna()
        return {"條件": name, "樣本": len(v20), "10日": round(v10.mean(), 2), "20日": round(v20.mean(), 2),
                "比平均": round(v20.mean() - base20, 2), "20日勝率": round((v20 > 0).mean() * 100, 1),
                "前半": round(a.mean() - F20.where(liquid & first).stack().mean(), 2),
                "後半": round(b.mean() - F20.where(liquid & ~first).stack().mean(), 2)}

    def show(title, items):
        print(f"\n===== {title}（隔天開盤買；全部平均 10日 {base10:.2f}%、20日 {base20:.2f}%；比平均＝20日減全部平均，前半／後半也是）=====")
        print(pd.DataFrame([stat(n, m) for n, m in items]).set_index("條件").to_string())


    # ===== 季報：只用「法定公布期限之後」的資料（Q1 5/15、Q2 8/14、Q3 11/14、Q4 隔年 3/31，隔天起才可用）=====
    raw_close = pv(prices, "close_price").reindex_like(close).ffill(limit=5)
    q = qr.copy()
    for c in ("eps", "roe", "book_value", "operating_cf", "free_cf"):
        q[c] = pd.to_numeric(q[c], errors="coerce")
    q["year"], q["quarter"] = q["year"].astype(int), q["quarter"].astype(int)
    avail = {1: "05-16", 2: "08-15", 3: "11-15"}
    q["avail"] = [f"{y + 1}-04-01" if qq == 4 else f"{y}-{avail[qq]}" for y, qq in zip(q["year"], q["quarter"])]
    key = q.set_index(["stock_id", "year", "quarter"])
    prev_same = key["eps"].reindex(list(zip(q["stock_id"], q["year"] - 1, q["quarter"]))).values
    prev_full = key["eps"].reindex(list(zip(q["stock_id"], q["year"] - 1, [4] * len(q)))).values
    q["eps_yoy"] = np.where(np.abs(prev_same) > 0, (q["eps"] - prev_same) / np.abs(prev_same) * 100, np.nan)
    q["ttm"] = np.where(q["quarter"] == 4, q["eps"], q["eps"] + prev_full - prev_same)
    q["roe_ann"] = q["roe"] * 4 / q["quarter"]
    q = q.sort_values(["avail", "year", "quarter"])

    def asof(col):
        w = q.pivot_table(index="avail", columns="stock_id", values=col, aggfunc="last")
        idx_all = sorted(set(w.index) | set(dates))
        return w.reindex(idx_all).ffill().reindex(sd).reindex(columns=close.columns)

    eps, yoy, roe, ttm, bv, ocf, fcf = (asof(c) for c in ("eps", "eps_yoy", "roe_ann", "ttm", "book_value", "operating_cf", "free_cf"))
    rc = W(raw_close)
    pe = (rc / ttm).where(ttm > 0)
    pb = (rc / bv).where(bv > 0)
    W2 = lambda m: m.reindex(index=sd, columns=close.columns)  # noqa: E731
    show("EPS（年初累計）", [("EPS > 0", eps > 0), ("EPS ≤ 0（虧損）", eps <= 0)])
    show("EPS 年增率（和去年同期累計比）", [
        ("≥ 50%", yoy >= 50), ("20～50%", (yoy >= 20) & (yoy < 50)), ("0～20%", (yoy >= 0) & (yoy < 20)),
        ("−20～0%", (yoy < 0) & (yoy >= -20)), ("< −20%", yoy < -20)])
    show("ROE（年化）", [
        ("≥ 20%", roe >= 20), ("15～20%", (roe >= 15) & (roe < 20)), ("10～15%", (roe >= 10) & (roe < 15)),
        ("5～10%", (roe >= 5) & (roe < 10)), ("0～5%", (roe >= 0) & (roe < 5)), ("< 0", roe < 0)])
    show("本益比（近 4 季 EPS）", [
        ("近 4 季虧損（沒有本益比）", ttm <= 0), ("< 10", pe < 10), ("10～15", (pe >= 10) & (pe < 15)),
        ("15～25", (pe >= 15) & (pe < 25)), ("25～40", (pe >= 25) & (pe < 40)), ("≥ 40", pe >= 40)])
    show("P/B（股價淨值比）", [
        ("< 1", pb < 1), ("1～1.5", (pb >= 1) & (pb < 1.5)), ("1.5～3", (pb >= 1.5) & (pb < 3)),
        ("3～6", (pb >= 3) & (pb < 6)), ("≥ 6", pb >= 6)])
    show("現金流（年初累計）", [
        ("營業現金流 > 0", ocf > 0), ("營業現金流 ≤ 0", ocf <= 0),
        ("自由現金流 > 0", fcf > 0), ("自由現金流 ≤ 0", fcf <= 0),
        ("EPS > 0 但營業現金流 ≤ 0", (eps > 0) & (ocf <= 0))])


if __name__ == "__main__":
    main()
