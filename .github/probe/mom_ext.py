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
    lowx = close / low52
    g20 = (close / close.shift(20) - 1) * 100
    offh = (close / high52 - 1) * 100
    for nm, sig in (("趨勢模板（全部，每天）", tt), ("今天剛符合趨勢模板", tt_new), ("52 週新高＋量增", bo)):
        show(f"{nm}：全體", [("全部", sig)])
        show(f"{nm}：離 50 日均線", [("< 5%", sig & (ext < 5)), ("5～10%", sig & (ext >= 5) & (ext < 10)),
             ("10～20%", sig & (ext >= 10) & (ext < 20)), ("20～30%", sig & (ext >= 20) & (ext < 30)), ("≥ 30%", sig & (ext >= 30))])
        show(f"{nm}：52 週低點的幾倍", [("1.3～1.6", sig & (lowx < 1.6)), ("1.6～2", sig & (lowx >= 1.6) & (lowx < 2)),
             ("2～3", sig & (lowx >= 2) & (lowx < 3)), ("≥ 3", sig & (lowx >= 3))])
        show(f"{nm}：近 20 日漲幅", [("< 5%", sig & (g20 < 5)), ("5～15%", sig & (g20 >= 5) & (g20 < 15)),
             ("15～30%", sig & (g20 >= 15) & (g20 < 30)), ("≥ 30%", sig & (g20 >= 30))])
        show(f"{nm}：RS", [("70～80", sig & (rs < 80)), ("80～90", sig & (rs >= 80) & (rs < 90)), ("≥ 90", sig & (rs >= 90))])
    show("趨勢模板：離 52 週高點", [("新高（0）", tt & (offh >= 0)), ("−5～0%", tt & (offh < 0) & (offh >= -5)),
         ("−15～−5%", tt & (offh < -5) & (offh >= -15)), ("−25～−15%", tt & (offh < -15))])
    # ===== 布林通道：碰過下軌後，收盤由下往上站上中軌 =====
    mid = close.rolling(20, min_periods=20).mean()
    sdv = close.rolling(20, min_periods=20).std(ddof=0)
    lower, upper = mid - 2 * sdv, mid + 2 * sdv
    cross = (close > mid) & (close.shift(1) <= mid.shift(1)) & liq_all
    touch = lambda n: (close <= lower).astype(float).shift(1).rolling(n, min_periods=1).max().fillna(0) > 0  # noqa: E731
    b10 = cross & touch(10)
    ma60 = close.rolling(60, min_periods=60).mean()
    width = (upper - lower) / mid * 100
    show("布林：站上中軌", [("只站上中軌（沒碰下軌）", cross & ~touch(20)), ("5 日內碰過下軌", cross & touch(5)),
         ("10 日內碰過下軌", b10), ("20 日內碰過下軌", cross & touch(20))])
    show("布林：10 日內碰下軌＋站上中軌，再加條件", [
        ("全部", b10), ("量比 ≥ 1.5", b10 & (vr >= 1.5)), ("中軌上揚", b10 & (mid > mid.shift(1))),
        ("收盤在 60 日線上", b10 & (close > ma60)), ("收盤在 200 日線上", b10 & (close > ma200)),
        ("收盤在 60 日線下", b10 & (close <= ma60)), ("RS ≥ 70", b10 & (rs >= 70)),
        ("通道寬 < 10%", b10 & (width < 10)), ("通道寬 ≥ 20%", b10 & (width >= 20)),
        ("同時符合趨勢模板", b10 & tt)])
    show("布林：近 20 日漲幅（10 日內碰下軌＋站上中軌）", [("< 0%", b10 & (g20 < 0)), ("0～10%", b10 & (g20 >= 0) & (g20 < 10)),
         ("≥ 10%", b10 & (g20 >= 10))])

    pull = tt & (ext >= 0) & (ext < 5) & (close.shift(5) > close)
    show("趨勢模板回檔候選", [("回到 50 日線 5% 以內、5 日下跌", pull), ("同上且 RS ≥ 80", pull & (rs >= 80))])


if __name__ == "__main__":
    main()
