"""
暫時的研究腳本（只讀資料庫，不合併）：D 段為同產業持股上限、均量門檻比較；
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
    stocks = pd.DataFrame(S._fetch_all(lambda: sb.table("stock_info").select("stock_id, industry").order("stock_id")))
    print(f"載入完成 {time.time() - t0:.0f} 秒；股價 {len(prices):,} 筆", flush=True)

    # ===== 逐日重算評分 =====
    t0 = time.time()
    feat = S.Features({"prices": prices[["stock_id", "date", "close_price", "volume"]], "adjust": adjust, "ins": ins,
                       "margin": margin, "holder": holder, "revenue": revenue, "stocks": stocks[["stock_id"]]})
    sd_list = [d for d in recent if d >= START]
    recs = []
    for d in sd_list:
        _captured.clear()
        rows = S.score_day(feat, d)
        for r, fl in zip(rows, _captured):
            if r["avg_volume_20"] >= 100:
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
    df = sc[sc["lots"] >= 500].merge(fl, on=["date", "stock_id"], how="inner")
    df["excess"] = df["fwd"] - df.groupby("date")["fwd"].transform("mean")
    days = sorted(df["date"].unique())
    mid_day = days[len(days) // 2]
    df["half"] = (df["date"] >= mid_day).astype(int)
    taiex_all = pd.to_numeric(idx.set_index("date")["close"])
    taiex = taiex_all.reindex(dates).ffill()
    print(f"\n報酬資料：{days[0]} ～ {days[-1]}（共 {len(days)} 個評分日、{len(df):,} 筆），前後兩段分界 {mid_day}")

    # ===== 模擬共用 =====
    cols = close.columns
    O, C = opn.values, close.values
    start_i = dates.index(next(d for d in dates if d >= START))
    mid_i = dates.index(next(d for d in dates if d >= mid_day))

    def wide(col):
        return sc.pivot(index="date", columns="stock_id", values=col).reindex(index=dates, columns=cols)
    ind = stocks.set_index("stock_id")["industry"].to_dict()
    grp = [ind.get(c) or None for c in cols]
    tie = (wide("chip").fillna(0) * 1e-3 + np.log1p(wide("lots").fillna(0)) * 1e-6)

    def above(ma):
        return (taiex_all > taiex_all.rolling(ma, min_periods=ma).mean()).reindex(dates).fillna(False).values

    def mkt_ok(ma):
        """ma：None 不過濾、數字、("and"/"or", [均線...])"""
        if ma is None:
            ok = np.ones(len(dates), dtype=bool)
        elif isinstance(ma, tuple):
            arr = [above(m) for m in ma[1]]
            ok = np.logical_and.reduce(arr) if ma[0] == "and" else np.logical_or.reduce(arr)
        else:
            ok = above(ma)
        return np.repeat(ok[:, None], len(cols), axis=1)

    def run(total, ma=60, top=10, half_top=None, hold=HOLD, cand=None, cap=None):
        """half_top：季線上但月線下的日子只買前 half_top 名（其餘照 top）"""
        tot = total + tie
        rk = tot.rank(axis=1, ascending=False, method="first")
        sig = (rk <= (cand or top)).values & mkt_ok(ma)
        if half_top is not None:
            weak = np.repeat((above(60) & ~above(20))[:, None], len(cols), axis=1)
            sig = np.where(weak, (rk <= half_top).values & mkt_ok(60), sig)
        trades, eq = simulate(sig, tot.fillna(-1e9).values, O, C, None, top, start_i, hold=hold, group=grp, cap=cap)
        return summarize(trades, eq, dates, start_i, mid_i)

    # ===== D. 均量門檻、同產業持股上限（月線和季線都在上才買、前 10 名、持有 10 天）=====
    both = ("and", [20, 60])
    lots = wide("lots")
    T_all = wide("total")
    print(f"\n加權指數同期：{(taiex.iloc[-1] / taiex.iloc[start_i] - 1) * 100:.1f}%"
          f"（前半 {(taiex.iloc[mid_i] / taiex.iloc[start_i] - 1) * 100:.1f}%、後半 {(taiex.iloc[-1] / taiex.iloc[mid_i] - 1) * 100:.1f}%）")
    print("\n===== D1. 20 日均量門檻（張）=====")
    rows = []
    for thr in (300, 500, 1000, 2000, 3000):
        T = T_all.where(lots >= thr)
        r = run(T, both)
        rows.append({"門檻": thr, **r})
    df_d = pd.DataFrame(rows).set_index("門檻")
    df_d["報酬回撤比"] = (df_d["年化%"] / -df_d["最大回撤%"]).round(2)
    print(df_d.to_string())
    T = T_all.where(lots >= 500)
    print("\n===== D2. 同產業最多持有幾檔（門檻 500 張）=====")
    rk10 = (T + tie).rank(axis=1, ascending=False, method="first") <= 10
    conc = []
    for i in range(start_i, len(dates)):
        sel = [grp[j] for j in np.where(rk10.values[i])[0]]
        if sel:
            conc.append(pd.Series(sel).value_counts().iloc[0])
    conc = pd.Series(conc)
    print(f"每天前 10 名裡，最多的同一產業有幾檔：平均 {conc.mean():.1f} 檔；≥3 檔的天數 {(conc >= 3).mean() * 100:.0f}%、"
          f"≥4 檔 {(conc >= 4).mean() * 100:.0f}%、≥5 檔 {(conc >= 5).mean() * 100:.0f}%")
    top_ind = pd.Series([grp[j] for i in range(start_i, len(dates)) for j in np.where(rk10.values[i])[0]]).value_counts().head(8)
    print("前 10 名最常出現的產業（人次）：" + "、".join(f"{k} {v}" for k, v in top_ind.items()))
    rows = [{"版本": "不限制（現在）", **run(T, both)}]
    for cap in (2, 3, 4):
        rows.append({"版本": f"同產業最多 {cap} 檔，超過就空著", **run(T, both, cap=cap)})
        rows.append({"版本": f"同產業最多 {cap} 檔，往下補到前 15 名", **run(T, both, cand=15, cap=cap)})
        rows.append({"版本": f"同產業最多 {cap} 檔，往下補到前 20 名", **run(T, both, cand=20, cap=cap)})
    df_c = pd.DataFrame(rows).set_index("版本")
    df_c["報酬回撤比"] = (df_c["年化%"] / -df_c["最大回撤%"]).round(2)
    print(df_c.to_string())
    return

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
    for ma, name in [(60, "季線上才買（現在）"), (None, "不過濾"), (20, "月線上才買"), (120, "半年線上才買"),
                     (("and", [20, 60]), "月線和季線都在上才買"), (("or", [20, 60]), "月線或季線其中一條在上就買")]:
        days_ok = mkt_ok(ma)[start_i:, 0].mean() * 100
        rows.append({"過濾": name, "可買天數%": round(days_ok, 0), **run(T, ma)})
    for half_top in (5, 3, 0):
        days_ok = mkt_ok(60)[start_i:, 0].mean() * 100
        rows.append({"過濾": f"季線上才買，月線下只買前 {half_top} 名", "可買天數%": round(days_ok, 0),
                     **run(T, 60, half_top=half_top)})
    print("（「月線下只買前 N 名」：季線上、月線下的日子新買進只買前 N 名，其餘空位留現金）")
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
