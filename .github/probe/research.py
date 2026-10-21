"""
暫時的研究腳本（只讀資料庫）：評分權重各因子的效果、規則 v4 候選、大盤濾網。
買法一律「訊號隔天收盤買進、持有 H 個交易日後收盤賣出」，扣 0.585% 交易成本。
用法：python .github/probe/research.py [開始日期]
"""
import os
import sys
import time

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "app"))

import signals  # noqa: E402

COST = 0.585
HS = (5, 20)
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 200)

# ---------- 因子：(名稱, 面向, v3 分數, 判斷) ----------
FACTORS = [
    ("外資5日買超", "chip", 4, lambda x: x["f5"] > 0),
    ("外資買超佔量5%", "chip", 3, lambda x: x["f5"] > 0 and x["vol5"] * 5 > 0 and x["f5"] / (x["vol5"] * 5) >= 0.05),
    ("外資連買3天", "chip", 3, lambda x: x["f_streak"] >= 3),
    ("投信5日買超", "chip", 7, lambda x: x["s5"] > 0),
    ("投信連買3天", "chip", 8, lambda x: x["s_streak"] >= 3),
    ("大戶週增", "chip", 4, lambda x: signals._num(x["holder_diff"]) and 0 < x["holder_diff"] < 0.5),
    ("大戶週增≥0.5", "chip", 7, lambda x: signals._num(x["holder_diff"]) and x["holder_diff"] >= 0.5),
    ("券資比≥10%", "margin", 8, lambda x: _ratio(x) >= 0.10),
    ("券資比5~10%", "margin", 4, lambda x: 0.05 <= _ratio(x) < 0.10),
    ("營收年增<20%", "fund", 4, lambda x: signals._num(x["yoy"]) and 0 < x["yoy"] < 20),
    ("營收年增≥20%", "fund", 8, lambda x: signals._num(x["yoy"]) and x["yoy"] >= 20),
    ("累計營收年增", "fund", 4, lambda x: signals._num(x["cum_yoy"]) and x["cum_yoy"] > 0),
    ("營收連3月年增", "fund", 10, lambda x: x["yoy3"] is True),
    ("營收創新高", "fund", 13, lambda x: x["new_high"] is True),
    ("站上月線", "tech", 10, lambda x: signals._num(x["ma20"]) and x["close"] > x["ma20"]),
    ("RSI 50~75", "tech", 4, lambda x: signals._num(x["rsi"]) and 50 <= x["rsi"] <= 75),
    ("近20日上漲", "tech", 11, lambda x: signals._num(x["chg20"]) and x["chg20"] > 0),
    # 以下 v3 沒有計分，只拿來研究
    ("近20日漲≥30%", "extra", 0, lambda x: signals._num(x["chg20"]) and x["chg20"] >= 0.30),
    ("近20日漲≥15%", "extra", 0, lambda x: signals._num(x["chg20"]) and x["chg20"] >= 0.15),
    ("RSI≥80", "extra", 0, lambda x: signals._num(x["rsi"]) and x["rsi"] >= 80),
    ("融資5日增≥20%", "extra", 0, lambda x: signals._num(x["mb_chg5"]) and x["mb_chg5"] >= 0.20),
]
NAMES = [f[0] for f in FACTORS]
V3 = {f[0]: f[2] for f in FACTORS}


def _ratio(x):
    mb, sb = x["mb"], x["sb"]
    return sb / mb if signals._num(mb) and mb > 0 and signals._num(sb) else -1


def collect(sb, start):
    """跑 signals 的特徵，記下每天每檔流動股票的因子（不寫資料庫）"""
    t = time.time()
    targets, data = signals.load_inputs(sb, start)
    print(f"載入 {time.time() - t:.0f} 秒")
    feat = signals.Features(data)
    rows = []
    orig = signals.score_stock

    def spy(x):
        if x["vol20"] / 1000 >= signals.LIQUID_MIN_LOTS:
            rows.append((cur["d"], cur["sid"](x), x["vol20"], *[bool(fn(x)) for _, _, _, fn in FACTORS]))
        return orig(x)

    # score_day 不會把股票代號傳給 score_stock，用 close 對回代號（同一天收盤價可能重複，改用順序）
    cur = {}
    signals.score_stock = spy
    try:
        for d in targets:
            if d not in feat.close.index:
                continue
            order = iter([s for s in feat.close.loc[d].dropna().index if not feat.stocks or s in feat.stocks])
            cur["d"], cur["sid"] = d, (lambda _x, it=order: next(it))
            signals.score_day(feat, d)
    finally:
        signals.score_stock = orig
    df = pd.DataFrame(rows, columns=["date", "stock_id", "vol20", *NAMES])
    print(f"因子 {len(df):,} 筆，{df['date'].nunique()} 天，{time.time() - t:.0f} 秒")
    return df, feat.close.ffill(limit=5), data


def add_returns(df, close):
    """隔天收盤買進、持有 h 天後收盤賣出的報酬（%，未扣成本）與當天全部流動股票的平均"""
    idx = {d: i for i, d in enumerate(close.index)}
    arr = close.to_numpy()
    col = {s: j for j, s in enumerate(close.columns)}
    for h in HS:
        out = np.full(len(df), np.nan)
        for k, (d, s) in enumerate(zip(df["date"], df["stock_id"])):
            i, j = idx.get(d), col.get(s)
            if i is None or j is None or i + 1 + h >= len(arr):
                continue
            b, f = arr[i + 1, j], arr[i + 1 + h, j]
            if b > 0 and f > 0:
                out[k] = (f / b - 1) * 100
        df[f"r{h}"] = out
        df[f"m{h}"] = df.groupby("date")[f"r{h}"].transform("mean")
        df[f"x{h}"] = df[f"r{h}"] - df[f"m{h}"]
    return df


def periods(df):
    dates = sorted(df["date"].unique())
    mid = dates[len(dates) // 2]
    return {"前半": df["date"] < mid, "後半": df["date"] >= mid, "全部": df["date"] >= ""}, mid


def factor_table(df):
    per, mid = periods(df)
    out = []
    for n in NAMES:
        row = {"因子": n, "v3分": V3[n], "比例": df[n].mean()}
        for p, m in per.items():
            for h in HS:
                d = df[m]
                row[f"{p}{h}日"] = d.loc[d[n], f"x{h}"].mean() - d.loc[~d[n], f"x{h}"].mean()
        out.append(row)
    print(f"\n===== 各因子：有 vs 沒有，之後超額報酬差（%），前半/後半分界 {mid} =====")
    print(pd.DataFrame(out).round(2).to_string(index=False))


def topn(df, weights, n=20, exclude=None):
    """每天依權重算總分取前 n 名，回傳每天 (淨報酬, 全市場平均)"""
    score = sum(df[k].astype(int) * w for k, w in weights.items() if w)
    d = df.assign(score=score)
    if exclude:
        d = d[~d[exclude]]
    d = d.sort_values(["date", "score", "vol20"], ascending=[True, False, False])
    top = d.groupby("date").head(n)
    res = {}
    for h in HS:
        g = top.groupby("date").agg(ret=(f"r{h}", "mean"), mkt=(f"m{h}", "mean")).dropna()
        g["net"] = g["ret"] - COST
        g["ex"] = g["net"] - g["mkt"]
        res[h] = g
    return res


def variants():
    v = dict(V3)
    out = {"v3（現行）": (v, None)}
    out["A 拿掉資金面"] = ({**v, "券資比≥10%": 0, "券資比5~10%": 0}, None)
    out["B 拿掉近20日上漲"] = ({**v, "近20日上漲": 0}, None)
    out["C 排除近20日漲≥30%"] = (v, "近20日漲≥30%")
    out["D 基本面加重（新高 13→20）"] = ({**v, "營收創新高": 20}, None)
    out["A+B"] = ({**v, "券資比≥10%": 0, "券資比5~10%": 0, "近20日上漲": 0}, None)
    out["A+C"] = ({**v, "券資比≥10%": 0, "券資比5~10%": 0}, "近20日漲≥30%")
    out["A+C+D"] = ({**v, "券資比≥10%": 0, "券資比5~10%": 0, "營收創新高": 20}, "近20日漲≥30%")
    return out


def variant_table(df, filt=None):
    per, _ = periods(df)
    rows = []
    for name, (w, ex) in variants().items():
        res = topn(df, w, exclude=ex)
        row = {"規則": name}
        for p, m in per.items():
            for h in HS:
                g = res[h]
                g = g[m.groupby(df["date"]).first().reindex(g.index).fillna(False)]
                row[f"{p}{h}日超額"] = g["ex"].mean()
                row[f"{p}{h}日勝"] = (g["ex"] > 0).mean()
        rows.append(row)
    print("\n===== 每天買前 20 名（隔天收盤買、扣成本）vs 全市場平均：每日平均超額報酬 %、贏大盤天數比例 =====")
    print(pd.DataFrame(rows).round(2).to_string(index=False))


# ---------- 大盤濾網 ----------
def market_filter(sb, dates):
    rows = signals._fetch_all(lambda: sb.table("market_index").select("date, close").eq("index_code", "TAIEX")
                              .order("date", desc=False))
    idx = pd.DataFrame(rows)
    idx["close"] = pd.to_numeric(idx["close"])
    idx = idx.set_index("date")["close"].sort_index()
    out = pd.DataFrame({"ma20": idx > idx.rolling(20).mean(), "ma60": idx > idx.rolling(60).mean()})
    return out.reindex(sorted(set(dates))).ffill()


def filter_report(name, daily, flt):
    """daily：每天的淨報酬（index 日期）；濾網不通過的日子不買（報酬 0）"""
    rows = []
    for h, g in daily.items():
        g = g.join(flt, how="left")
        for f in ("ma20", "ma60"):
            on = g[f].fillna(True).astype(bool)
            rows.append({"策略": name, "持有": h, "濾網": f"大盤在{f[2:]}日線上才買",
                         "不用濾網 平均淨報酬": g["net"].mean(),
                         "濾網通過時": g.loc[on, "net"].mean(), "濾網擋掉時": g.loc[~on, "net"].mean(),
                         "用濾網 平均淨報酬": g["net"].where(on, 0).mean(),
                         "擋掉天數比例": (~on).mean(), "樣本天數": len(g)})
    return rows


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "2025-02-03"
    sb = signals._client() if os.getenv("SUPABASE_URL") else None
    if sb is None:
        sys.path.insert(0, os.path.join(ROOT, "tests"))
        import conftest  # noqa: F401
        sb = conftest.make_client()
    df, close, data = collect(sb, start)
    df = add_returns(df, close)
    factor_table(df)
    variant_table(df)

    flt = market_filter(sb, df["date"])
    rows = []
    v3 = topn(df, V3)
    rows += filter_report("評分前20名", {h: g for h, g in v3.items()}, flt)

    # 葛蘭碧法則 2（預設參數，隔天收盤買、扣成本）
    from utils.analysis import GRANVILLE_DEFAULTS
    from utils.granville import rule2_events
    prices = signals.apply_adjustments(data["prices"], data["adjust"])
    ins = data["ins"][["stock_id", "date", "foreign_net_shares"]]
    ev = rule2_events(prices, ins=ins, min_volume=500, days=len(close.index), **GRANVILLE_DEFAULTS)
    ev = ev[ev["signal_date"] >= start].rename(columns={"signal_date": "date"})
    ev = add_returns(ev.assign(vol20=0), close)
    print(f"\n葛蘭碧法則 2 買點 {len(ev)} 個")
    g = {h: ev.dropna(subset=[f"r{h}"]).assign(net=lambda d, h=h: d[f"r{h}"] - COST).set_index("date")[["net"]]
         for h in HS}
    for h in HS:
        print(f"  持有 {h} 日：平均淨報酬 {g[h]['net'].mean():+.2f}%，勝率 {(g[h]['net'] > 0).mean():.0%}，"
              f"平均超額 {(ev[f'x{h}']).mean() - COST:+.2f}%")
    rows += filter_report("葛蘭碧法則2（每筆）", g, flt)

    # 動能（momentum.compute 重算）
    import momentum
    m_targets, m_prices, m_adj, _ = momentum.load_inputs(sb, start)
    mrows = pd.DataFrame(momentum.compute(m_prices, m_adj, m_targets))
    if not mrows.empty:
        mrows = mrows[mrows["liquid"]]
        for label, cond in [("動能 兩者同時", mrows["trend_template"] & mrows["breakout"]),
                            ("動能 52週新高＋量增", mrows["breakout"]), ("動能 剛符合趨勢模板", mrows["trend_new"])]:
            e = add_returns(mrows[cond][["stock_id", "date"]].assign(vol20=0), close)
            gg = {h: e.dropna(subset=[f"r{h}"]).assign(net=lambda d, h=h: d[f"r{h}"] - COST)
                  .set_index("date")[["net"]] for h in HS}
            print(f"{label} {len(e)} 筆：" + "，".join(
                f"{h} 日平均淨報酬 {gg[h]['net'].mean():+.2f}%" for h in HS))
            rows += filter_report(label + "（每筆）", gg, flt)
    print("\n===== 大盤濾網（加權指數收盤在 20／60 日均線之上才買；擋掉的日子當作不買、報酬 0）=====")
    print(pd.DataFrame(rows).round(2).to_string(index=False))


if __name__ == "__main__":
    main()
