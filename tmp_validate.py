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

sb = s._client()
targets, data = s.load_inputs(sb, "2025-02-05", None)
feat = s.Features(data)
fwd = feat.close_ff.shift(-HORIZON) / feat.close_ff - 1

recs = []
for d in targets:
    if d not in fwd.index:
        continue
    f_today = fwd.loc[d]
    for r in s.score_day(feat, d):
        if not r["liquid"]:
            continue
        ret = f_today.get(r["stock_id"], np.nan)
        if not np.isfinite(ret):
            continue
        row = {"date": d, "ret": ret, "chip": r["chip_score"], "margin": r["margin_score"],
               "fund": r["fund_score"], "tech": r["tech_score"], "total": r["total_score"]}
        for k in FACTORS:
            row[k] = any(x.startswith(k) for x in r["reasons"])
        recs.append(row)

df = pd.DataFrame(recs)
df["ex"] = df["ret"] - df.groupby("date")["ret"].transform("mean")
df["period"] = np.where(df["date"] < "2025-11-01", "A 2025/02-10", "B 2025/11-2026/09")
print(f"\n樣本：{df['date'].nunique()} 天，{len(df):,} 筆（{df['date'].min()} ～ {df['date'].max()}）")

print("\n=== 各面向分數四分位：之後 20 日超額報酬 % ===")
for comp in ("chip", "margin", "fund", "tech", "total"):
    df["q"] = df.groupby("date")[comp].transform(
        lambda v: pd.qcut(v.rank(method="first"), 4, labels=False))
    t = df.groupby(["period", "q"])["ex"].mean().unstack() * 100
    t.loc["全部"] = df.groupby("q")["ex"].mean() * 100
    ic = df.groupby("date").apply(lambda g: g[comp].corr(g["ret"], method="spearman"))
    print(f"\n[{comp}] 每日排名相關 IC 平均 {ic.mean():+.3f}（IC>0 的天數 {(ic > 0).mean():.0%}）")
    print(t.round(2).to_string())

print("\n=== 各因子：有加分 vs 沒加分 的 20 日超額報酬差（%），與加分比例 ===")
out = []
for k, comp in FACTORS.items():
    row = {"因子": k, "面向": comp, "加分比例": f"{df[k].mean():.0%}"}
    for p, g in [("全部", df)] + list(df.groupby("period")):
        row[p] = round((g.loc[g[k], "ex"].mean() - g.loc[~g[k], "ex"].mean()) * 100, 2)
    out.append(row)
print(pd.DataFrame(out).to_string(index=False))
