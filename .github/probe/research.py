"""
暫時的研究腳本（只讀資料庫，不合併）：
  A. 評分四大項（籌碼、資金、基本面、技術）與各個加分條件，哪些真的和之後 10 個交易日的報酬有關
  B. 大盤季線過濾（大盤在 60 日線下就不買）值不值得，和不過濾、20 日線、120 日線比較
評分用目前規則（v5）在記憶體逐日重算，不寫資料庫。報酬一律「評分隔天開盤買、第 10 個交易日後開盤賣」，
還原權值，扣來回成本 0.585%。法人資料 2025-01 起、融資和大戶 2025-10 起，之前這兩項沒有分數。
"""
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import signals as S  # noqa: E402
from sim import simulate, summarize  # noqa: E402

START = "2025-02-03"
HOLD = 10
COST = 0.585
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 400)

FLAGS = {
    "外資5日買超(+4)": lambda x: x["f5"] > 0,
    "外資買超佔量≥5%(+3)": lambda x: x["f5"] > 0 and x["vol5"] * 5 > 0 and x["f5"] / (x["vol5"] * 5) >= 0.05,
    "外資連買≥3天(+3)": lambda x: x["f_streak"] >= 3,
    "投信5日買超(+7)": lambda x: x["s5"] > 0,
    "投信連買≥3天(+8)": lambda x: x["s_streak"] >= 3,
    "大戶4週增≥1(+7)": lambda x: S._num(x["holder_diff4"]) and x["holder_diff4"] >= 1,
    "券資比≥10%(+8)": lambda x: S._num(x["mb"]) and x["mb"] > 0 and S._num(x["sb"]) and x["sb"] / x["mb"] >= 0.10,
    "券資比5～10%(+4)": lambda x: S._num(x["mb"]) and x["mb"] > 0 and S._num(x["sb"]) and 0.05 <= x["sb"] / x["mb"] < 0.10,
    "營收年增>0(+4/8)": lambda x: S._num(x["yoy"]) and x["yoy"] > 0,
    "營收年增≥20%(+8)": lambda x: S._num(x["yoy"]) and x["yoy"] >= 20,
    "累計營收年增(+4)": lambda x: S._num(x["cum_yoy"]) and x["cum_yoy"] > 0,
    "營收連3月年增(+10)": lambda x: x["yoy3"] is True,
    "營收創新高(+13)": lambda x: x["new_high"] is True,
    "站上月線(+10)": lambda x: S._num(x["ma20"]) and x["close"] > x["ma20"],
    "RSI≥70(+4)": lambda x: S._num(x["rsi"]) and x["rsi"] >= 70,
    "近20日上漲(+11)": lambda x: S._num(x["chg20"]) and x["chg20"] > 0,
}
_captured = []
_orig = S.score_stock


def _wrapped(x):
    _captured.append([bool(f(x)) for f in FLAGS.values()])
    return _orig(x)


S.score_stock = _wrapped


def stats(df, col, label):
    """flag 為 True 的股票比當天全體平均多幾 %（超額報酬），前後兩段分開"""
    out = {"條件": label}
    for name, part in (("全期", df), ("前半", df[df["half"] == 0]), ("後半", df[df["half"] == 1])):
        on = part[part[col]]
        out[f"{name}超額%"] = round(on["excess"].mean(), 2) if len(on) else np.nan
        if name == "全期":
            out["佔比%"] = round(len(on) / len(part) * 100, 1)
    return out


def main():
    from supabase import create_client
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
    all_dates = S.trading_dates(sb)
    recent = [d for d in all_dates if d >= "2024-11-01"]
    t0 = time.time()
    prices = S.load_daily(sb, "daily_prices", "stock_id, date, open_price, close_price, volume", recent)
    adjust = pd.DataFrame(S._fetch_all(
        lambda: sb.table("price_adjustments").select("stock_id, date, event_type, factor")
            .gt("date", recent[0]).order("date").order("stock_id").order("event_type")))
    idx = pd.DataFrame(S._fetch_all(lambda: sb.table("market_index").select("date, close").eq("index_code", "TAIEX").order("date")))
    ins = S.load_daily(sb, "stock_ins_daily", "stock_id, date, foreign_net_shares, sitca_net_shares",
                       [d for d in recent if d >= "2025-01-01"])
    margin = S.load_daily(sb, "margin_utilization", "stock_id, date, margin_balance, short_balance, margin_rate",
                          [d for d in recent if d >= "2025-10-01"])
    holder = pd.DataFrame(S._fetch_all(lambda: sb.table("holder_distribution").select("stock_id, week_date, big_holder_ratio")
                                       .order("week_date").order("stock_id")))
    revenue = pd.DataFrame(S._fetch_all(
        lambda: sb.table("monthly_revenue").select("stock_id, year_month, revenue, yoy_growth, cumulative_yoy_growth")
            .order("year_month").order("stock_id")))
    stocks = pd.DataFrame(S._fetch_all(lambda: sb.table("stock_info").select("stock_id").order("stock_id")))
    print(f"載入完成 {time.time() - t0:.0f} 秒；股價 {len(prices):,} 筆", flush=True)

    # ===== 逐日重算評分 =====
    t0 = time.time()
    feat = S.Features({"prices": prices[["stock_id", "date", "close_price", "volume"]], "adjust": adjust, "ins": ins,
                       "margin": margin, "holder": holder, "revenue": revenue, "stocks": stocks})
    sd_list = [d for d in recent if d >= START]
    recs = []
    for d in sd_list:
        _captured.clear()
        rows = S.score_day(feat, d)
        for r, fl in zip(rows, _captured):
            if r["liquid"]:
                recs.append((d, r["stock_id"], r["total_score"], r["chip_score"], r["margin_score"], r["fund_score"],
                             r["tech_score"], r["avg_volume_20"], *fl))
    sc = pd.DataFrame(recs, columns=["date", "stock_id", "total", "chip", "margin", "fund", "tech", "lots", *FLAGS])
    print(f"評分重算完成 {time.time() - t0:.0f} 秒，{sc['date'].nunique()} 天、{len(sc):,} 筆", flush=True)

    # ===== 價格寬表與 10 日後報酬 =====
    pv = S._pivot
    adj = S.apply_adjustments(prices, adjust)
    close = pv(adj, "close_price")
    mult = (pv(adj, "close_price") / pv(prices, "close_price")).reindex_like(close).ffill()
    opn = (pv(prices, "open_price").reindex_like(close) * mult).where(lambda x: x > 0)
    dates = list(close.index)
    fwd = (opn.shift(-(HOLD + 1)) / opn.shift(-1) - 1) * 100 - COST   # 第 t 天評分：t+1 開盤買、t+1+HOLD 開盤賣
    fl = fwd.stack().rename("fwd").reset_index()
    fl.columns = ["date", "stock_id", "fwd"]
    df = sc.merge(fl, on=["date", "stock_id"], how="inner")
    df["excess"] = df["fwd"] - df.groupby("date")["fwd"].transform("mean")
    days = sorted(df["date"].unique())
    mid_day = days[len(days) // 2]
    df["half"] = (df["date"] >= mid_day).astype(int)
    taiex_all = pd.to_numeric(idx.set_index("date")["close"])
    taiex = taiex_all.reindex(dates).ffill()
    print(f"\n報酬資料：{days[0]} ～ {days[-1]}（共 {len(days)} 個評分日、{len(df):,} 筆），前後兩段分界 {mid_day}")

    # ===== A1. 四大項：每天排名相關（Spearman）=====
    print("\n===== A1. 四大項與總分：每天分數和之後 10 日報酬的排名相關（越大越有用，0 代表沒關係）=====")
    rows = []
    for col in ["total", "chip", "margin", "fund", "tech"]:
        ic = df.groupby("date").apply(lambda g: g[col].rank().corr(g["fwd"].rank()) if g[col].nunique() > 1 else np.nan).dropna()
        h = pd.Series(ic.index >= mid_day, index=ic.index)
        rows.append({"項目": col, "天數": len(ic), "平均相關": round(ic.mean(), 4), "相關>0 的天數%": round((ic > 0).mean() * 100, 1),
                     "前半": round(ic[~h].mean(), 4), "後半": round(ic[h].mean(), 4)})
    print(pd.DataFrame(rows).to_string(index=False))

    # ===== A2. 各加分條件 =====
    print("\n===== A2. 各加分條件：符合的股票比當天全體平均多幾 %（之後 10 日，已扣成本）=====")
    print(pd.DataFrame([stats(df, k, k) for k in FLAGS]).to_string(index=False))
    late = df[df["date"] >= "2025-11-01"]
    print("\n（融資、大戶只有 2025-11 起，另外看這段）")
    lmid = sorted(late["date"].unique())[len(late["date"].unique()) // 2]
    late = late.assign(half=(late["date"] >= lmid).astype(int))
    print(pd.DataFrame([stats(late, k, k) for k in ["大戶4週增≥1(+7)", "券資比≥10%(+8)", "券資比5～10%(+4)"]]).to_string(index=False))

    # ===== 模擬共用 =====
    cols = close.columns
    O, C = opn.values, close.values
    start_i = dates.index(next(d for d in dates if d >= START))
    mid_i = dates.index(next(d for d in dates if d >= mid_day))

    def wide(col):
        return sc.pivot(index="date", columns="stock_id", values=col).reindex(index=dates, columns=cols)
    tie = (wide("chip").fillna(0) * 1e-3 + np.log1p(wide("lots").fillna(0)) * 1e-6)

    def mkt_ok(ma):
        if ma is None:
            return np.ones((len(dates), len(cols)), dtype=bool)
        ok = (taiex_all > taiex_all.rolling(ma, min_periods=ma).mean()).reindex(dates).fillna(False).values
        return np.repeat(ok[:, None], len(cols), axis=1)

    def run(total, ma=60, top=10):
        tot = total + tie
        rk = tot.rank(axis=1, ascending=False, method="first")
        sig = (rk <= top).values & mkt_ok(ma)
        trades, eq = simulate(sig, tot.fillna(-1e9).values, O, C, None, top, start_i, hold=HOLD)
        return summarize(trades, eq, dates, start_i, mid_i)

    # ===== A3. 拿掉某一大項 =====
    print(f"\n===== A3. 拿掉某一大項後的前 10 名（大盤在季線上才買、10 檔等額、持有 {HOLD} 日）=====")
    T = wide("total")
    rows = [{"版本": "現在（全部）", **run(T)}]
    for col, name in [("chip", "拿掉籌碼"), ("margin", "拿掉資金（券資比）"), ("fund", "拿掉基本面"), ("tech", "拿掉技術面")]:
        rows.append({"版本": name, **run(T - wide(col).fillna(0))})
    for col, name in [("chip", "籌碼加倍"), ("fund", "基本面加倍"), ("tech", "技術面加倍")]:
        rows.append({"版本": name, **run(T + wide(col).fillna(0))})
    print(pd.DataFrame(rows).set_index("版本").to_string())

    # ===== B. 大盤過濾 =====
    print(f"\n加權指數同期：{(taiex.iloc[-1] / taiex.iloc[start_i] - 1) * 100:.1f}%"
          f"（前半 {(taiex.iloc[mid_i] / taiex.iloc[start_i] - 1) * 100:.1f}%、後半 {(taiex.iloc[-1] / taiex.iloc[mid_i] - 1) * 100:.1f}%）")
    print("\n===== B1. 大盤過濾：前 10 名、10 檔等額、持有 10 日 =====")
    rows = []
    for ma, name in [(60, "季線上才買（現在）"), (None, "不過濾"), (20, "月線上才買"), (120, "半年線上才買")]:
        days_ok = mkt_ok(ma)[start_i:, 0].mean() * 100
        rows.append({"過濾": name, "可買天數%": round(days_ok, 0), **run(T, ma)})
    print(pd.DataFrame(rows).set_index("過濾").to_string())

    print("\n===== B2. 每天前 10 名之後 10 日的平均報酬，依當天大盤在季線上／下分開 =====")
    top10 = df.assign(rk=df.groupby("date")["total"].rank(ascending=False, method="first"))
    top10 = top10[top10["rk"] <= 10].copy()
    state = (taiex_all > taiex_all.rolling(60, min_periods=60).mean()).rename("above")
    top10["above"] = top10["date"].map(state)
    tx_fwd = (taiex.shift(-(HOLD + 1)) / taiex.shift(-1) - 1) * 100
    top10["taiex_fwd"] = top10["date"].map(tx_fwd)
    rows = []
    for (h, above), g in top10.groupby(["half", "above"]):
        rows.append({"期間": "前半" if h == 0 else "後半", "大盤": "季線上" if above else "季線下", "天數": g["date"].nunique(),
                     "筆數": len(g), "平均報酬%": round(g["fwd"].mean(), 2), "勝率%": round((g["fwd"] > 0).mean() * 100, 1),
                     "比全體多%": round(g["excess"].mean(), 2), "同期大盤%": round(g.drop_duplicates("date")["taiex_fwd"].mean(), 2)})
    for above, g in top10.groupby("above"):
        rows.append({"期間": "全期", "大盤": "季線上" if above else "季線下", "天數": g["date"].nunique(), "筆數": len(g),
                     "平均報酬%": round(g["fwd"].mean(), 2), "勝率%": round((g["fwd"] > 0).mean() * 100, 1),
                     "比全體多%": round(g["excess"].mean(), 2), "同期大盤%": round(g.drop_duplicates("date")["taiex_fwd"].mean(), 2)})
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
