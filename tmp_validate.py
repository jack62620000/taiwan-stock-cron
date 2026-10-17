"""暫時：用 2025-02 起的資料驗證各評分因子（只讀資料庫，不寫入）"""
import numpy as np
import pandas as pd

import signals as s

HORIZON = 20
FACTORS = {
    "外資5日買超": "chip", "外資買超佔成交量": "chip", "外資連買": "chip",
    "投信5日買超": "chip", "投信連買": "chip", "大戶持股週增": "chip",
    "融資使用率低": "margin", "資減價漲": "margin", "券資比": "margin",
    "月營收年增": "fund", "累計營收年增": "fund", "營收連3月年增": "fund", "營收創": "fund",
    "站上月線": "tech", "RSI 強勢區": "tech", "近20日上漲": "tech",
}

_orig = s.score_stock


def old_margin_pts(x):
    """v2 融資使用率、資減價漲兩項給的分數（含中性分）"""
    old_margin = 0
    rate, mb_chg5 = x["rate"], x["mb_chg5"]
    old_margin += 4 if not s._num(rate) else (8 if rate < 10 else (4 if rate < 20 else 0))
    if not s._num(mb_chg5):
        old_margin += 4
    elif mb_chg5 < 0 and x["chg5"] > 0:
        old_margin += 9
    elif mb_chg5 <= 0.05:
        old_margin += 4
    return old_margin


def variants(x, reasons, total):
    has = lambda k: any(r.startswith(k) for r in reasons)
    base = total - old_margin_pts(x)
    return {
        # 只拿掉兩個融資因子
        "v3a": base,
        # 17 分給投信與技術面
        "v3b": base + 3 * has("投信5日買超") + 4 * has("投信連買") + 4 * has("站上月線") + 6 * has("近20日上漲"),
        # 17 分給月營收（連 3 月年增 +6、月營收年增 +5、累計年增 +6）
        "v3c": base + 6 * has("營收連3月年增") + 5 * has("月營收年增") + 6 * has("累計營收年增"),
    }


sb = s._client()
targets, data = s.load_inputs(sb, "2025-02-05", None)
feat = s.Features(data)
fwd = feat.close_ff.shift(-HORIZON) / feat.close_ff - 1

recs = []
for d in targets:
    if d not in fwd.index:
        continue
    f_today = fwd.loc[d]
    xs = {}
    def _cap(x, _o=_orig):
        res = _o(x)
        xs[len(xs)] = x
        return res
    s.score_stock = _cap
    day_rows = s.score_day(feat, d)
    for i, r in enumerate(day_rows):
        r["_x"] = xs[i]
    for r in day_rows:
        if not r["liquid"]:
            continue
        ret = f_today.get(r["stock_id"], np.nan)
        if not np.isfinite(ret):
            continue
        row = {"date": d, "ret": ret, "chip": r["chip_score"], "margin": r["margin_score"],
               "fund": r["fund_score"], "tech": r["tech_score"], "total": r["total_score"],
               **variants(r["_x"], r["reasons"], r["total_score"])}
        for k in FACTORS:
            row[k] = any(x.startswith(k) for x in r["reasons"])
        recs.append(row)

df = pd.DataFrame(recs)
df["ex"] = df["ret"] - df.groupby("date")["ret"].transform("mean")
df["period"] = np.where(df["date"] < "2025-11-01", "A 2025/02-10", "B 2025/11-2026/09")
print(f"\n樣本：{df['date'].nunique()} 天，{len(df):,} 筆（{df['date'].min()} ～ {df['date'].max()}）")

print("\n=== 各面向分數四分位：之後 20 日超額報酬 % ===")
for comp in ("chip", "margin", "fund", "tech", "total", "v3a", "v3b", "v3c"):
    df["q"] = df.groupby("date")[comp].transform(
        lambda v: pd.qcut(v.rank(method="first"), 4, labels=False))
    t = df.groupby(["period", "q"])["ex"].mean().unstack() * 100
    t.loc["全部"] = df.groupby("q")["ex"].mean() * 100
    ic = df.groupby("date").apply(lambda g: g[comp].rank().corr(g["ret"].rank()))
    print(f"\n[{comp}] 每日排名相關 IC 平均 {ic.mean():+.3f}（IC>0 的天數 {(ic > 0).mean():.0%}）")
    print(t.round(2).to_string())

print("\n=== 每天分數最高 20 檔（同分依籌碼分、成交量）：平均 20 日超額報酬 % ===")
for comp in ("total", "v3a", "v3b", "v3c"):
    top = df.sort_values([comp, "chip"], ascending=False).groupby("date").head(20)
    by = top.groupby("period")["ex"].mean() * 100
    hit = (top.groupby("date")["ex"].mean() > 0).mean()
    print(f"  {comp}: 全部 {top['ex'].mean() * 100:+.2f}｜" + "｜".join(f"{k} {v:+.2f}" for k, v in by.items()) + f"｜贏過大盤的天數 {hit:.0%}")

print("\n=== 各因子：有加分 vs 沒加分 的 20 日超額報酬差（%），與加分比例 ===")
out = []
for k, comp in FACTORS.items():
    row = {"因子": k, "面向": comp, "加分比例": f"{df[k].mean():.0%}"}
    for p, g in [("全部", df)] + list(df.groupby("period")):
        row[p] = round((g.loc[g[k], "ex"].mean() - g.loc[~g[k], "ex"].mean()) * 100, 2)
    out.append(row)
print(pd.DataFrame(out).to_string(index=False))
