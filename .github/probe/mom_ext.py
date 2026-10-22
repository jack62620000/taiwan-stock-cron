"""
暫時的研究腳本（只讀資料庫）：動能選股入選時「已經漲多少」和之後報酬的關係。
隔天開盤買，看 20／60 個交易日相對大盤（大盤也用隔天開盤）的超額報酬。
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
    prices = S.load_daily(sb, "daily_prices", "stock_id, date, open_price, close_price, volume", recent)
    prices = prices[prices["stock_id"].astype(str).str.match(STOCK_PATTERN)]
    adjust = pd.DataFrame(S._fetch_all(
        lambda: sb.table("price_adjustments").select("stock_id, date, event_type, factor")
            .gt("date", recent[0]).order("date").order("stock_id").order("event_type")))
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

    ma50 = close.rolling(50, min_periods=50).mean()
    ma150 = close.rolling(150, min_periods=150).mean()
    ma200 = close.rolling(200, min_periods=200).mean()
    high52 = close.rolling(252, min_periods=252).max()
    low52 = close.rolling(252, min_periods=252).min()
    vol20 = vol.rolling(20, min_periods=20).mean()
    liq_all = vol20 >= LIQ
    rs_raw = sum(w * close / close.shift(n) for w, n in ((0.4, 63), (0.2, 126), (0.2, 189), (0.2, 252)))
    rs = (rs_raw.where(liq_all).rank(axis=1, pct=True) * 100).clip(upper=99).apply(np.floor)
    checks = [(close > ma150) & (close > ma200), ma150 > ma200, ma200 > ma200.shift(20),
              (ma50 > ma150) & (ma50 > ma200), close > ma50, close >= low52 * 1.3, close >= high52 * 0.75, rs >= 70]
    tt = (sum(c.astype(int) for c in checks) == 8) & liq_all
    tt_new = tt & ~tt.shift(1, fill_value=False)
    vr = vol / vol.rolling(50, min_periods=50).mean().shift(1)
    raw_b = (close > high52.shift(1)) & (vr >= 1.5) & liq_all
    bo = raw_b & (raw_b.astype(float).shift(1).rolling(10, min_periods=1).max().fillna(0) == 0)

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

    def stat(name, m):
        m = W(m.astype(float)).fillna(0).astype(bool) & liquid
        v20, v60 = F20.where(m).stack().dropna(), F60.where(m).stack().dropna()
        a = F20.where(m & first).stack().dropna()
        b = F20.where(m & ~first).stack().dropna()
        return {"條件": name, "樣本": len(v20), "20日": round(v20.mean(), 2), "20日比平均": round(v20.mean() - base20, 2),
                "20日勝率": round((v20 > 0).mean() * 100, 1), "60日": round(v60.mean(), 2),
                "60日比平均": round(v60.mean() - base60, 2), "60日勝率": round((v60 > 0).mean() * 100, 1),
                "前半20": round(a.mean() - F20.where(liquid & first).stack().mean(), 2),
                "後半20": round(b.mean() - F20.where(liquid & ~first).stack().mean(), 2)}

    def show(title, items):
        print(f"\n===== {title}（全部平均 20日 {base20:.2f}%、60日 {base60:.2f}%，都是超額報酬）=====")
        print(pd.DataFrame([stat(n, m) for n, m in items]).set_index("條件").to_string())

    ext = (close / ma50 - 1) * 100
    g20 = (close / close.shift(20) - 1) * 100
    ma5 = close.rolling(5, min_periods=5).mean()
    ma10 = close.rolling(10, min_periods=10).mean()
    ma20 = close.rolling(20, min_periods=20).mean()
    ma60 = close.rolling(60, min_periods=60).mean()
    pos52 = (close - low52) / (high52 - low52)       # 0 = 52 週低點，1 = 52 週高點
    L = liq_all
    show("對照：現在的動能選股", [("趨勢模板（每天）", tt), ("今天剛符合趨勢模板", tt_new), ("52 週新高＋量增", bo),
         ("趨勢模板且離 50 日線 < 30%", tt & (ext < 30))])

    # ===== 5MA 突破 10MA、20MA =====
    up10 = (ma5 > ma10) & (ma5.shift(1) <= ma10.shift(1))
    up20 = (ma5 > ma20) & (ma5.shift(1) <= ma20.shift(1))
    both = (ma5 > ma10) & (ma5 > ma20)
    gx = both & ~both.shift(1, fill_value=False) & L   # 今天第一天同時在 10、20 MA 之上
    show("5MA 突破 10MA、20MA", [
        ("5MA 穿過 10MA", up10 & L), ("5MA 穿過 20MA", up20 & L), ("5MA 第一天同時在 10、20MA 上", gx),
        ("同上＋量比 ≥ 1.5", gx & (vr >= 1.5)), ("同上＋20MA 上揚", gx & (ma20 > ma20.shift(1))),
        ("同上＋收盤在 60MA 上", gx & (close > ma60)), ("同上＋收盤在 200MA 上", gx & (close > ma200)),
        ("同上＋收盤在 60MA 下", gx & (close <= ma60)), ("同上＋RS ≥ 70", gx & (rs >= 70)),
        ("同上＋符合趨勢模板", gx & tt), ("同上＋52 週位置 < 30%（低檔）", gx & (pos52 < 0.3))])

    # ===== 低檔盤整後突破 =====
    hi20p = close.rolling(20, min_periods=20).max().shift(1)
    lo20p = close.rolling(20, min_periods=20).min().shift(1)
    rng = (hi20p / lo20p - 1) * 100                  # 前 20 日的高低差
    brk = (close > hi20p) & L
    hi40p = close.rolling(40, min_periods=40).max().shift(1)
    lo40p = close.rolling(40, min_periods=40).min().shift(1)
    rng40 = (hi40p / lo40p - 1) * 100
    brk40 = (close > hi40p) & L
    for lab, low_m in (("52 週位置 < 30%", pos52 < 0.3), ("52 週位置 < 50%", pos52 < 0.5), ("收盤在 200MA 下", close < ma200)):
        show(f"低檔盤整後突破（低檔＝{lab}）", [
            ("突破前 20 日高點", brk & low_m), ("前 20 日高低差 < 10%", brk & low_m & (rng < 10)),
            ("前 20 日高低差 < 15%", brk & low_m & (rng < 15)), ("同上＋量比 ≥ 1.5", brk & low_m & (rng < 15) & (vr >= 1.5)),
            ("同上＋量比 ≥ 2", brk & low_m & (rng < 15) & (vr >= 2)),
            ("前 40 日高低差 < 15%", brk40 & low_m & (rng40 < 15)), ("同上＋量比 ≥ 1.5", brk40 & low_m & (rng40 < 15) & (vr >= 1.5)),
            ("前 40 日高低差 < 20%＋量比 ≥ 1.5", brk40 & low_m & (rng40 < 20) & (vr >= 1.5))])
    show("盤整後突破（不限低檔）", [
        ("前 20 日高低差 < 15%＋量比 ≥ 1.5", brk & (rng < 15) & (vr >= 1.5)),
        ("前 40 日高低差 < 20%＋量比 ≥ 1.5", brk40 & (rng40 < 20) & (vr >= 1.5)),
        ("前 40 日高低差 < 20%＋量比 ≥ 1.5＋200MA 上", brk40 & (rng40 < 20) & (vr >= 1.5) & (close > ma200)),
        ("前 40 日高低差 < 20%＋量比 ≥ 1.5＋RS ≥ 70", brk40 & (rng40 < 20) & (vr >= 1.5) & (rs >= 70))])
    _ = g20

if __name__ == "__main__":
    main()
