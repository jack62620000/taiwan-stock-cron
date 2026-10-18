"""暫時：比較 v3 與 v4（外資持股 20 日增取代外資買超三項）每日前 20 名（只讀資料庫，不寫入）"""
import numpy as np
import pandas as pd

import signals as s

HORIZON = 20
_orig_load = s.load_daily


def _load(sb, table, columns, dates):
    if table == "stock_ins_daily":
        columns += ", foreign_hold_ratio"
    return _orig_load(sb, table, columns, dates)


s.load_daily = _load
sb = s._client()
targets, data = s.load_inputs(sb, "2025-02-05", None)
feat = s.Features(data)
fwd = feat.close_ff.shift(-HORIZON) / feat.close_ff - 1
ratio = s._pivot(data["ins"].dropna(subset=["foreign_hold_ratio"]), "foreign_hold_ratio") \
    .reindex(feat.close.index).ffill(limit=5)
hold20 = ratio - ratio.shift(20)

recs = []
for d in targets:
    if d not in fwd.index:
        continue
    f_today, h_today = fwd.loc[d], hold20.loc[d]
    for r in s.score_day(feat, d):
        if not r["liquid"]:
            continue
        ret = f_today.get(r["stock_id"], np.nan)
        if not np.isfinite(ret):
            continue
        has = lambda k: any(x.startswith(k) for x in r["reasons"])
        old = 4 * has("外資5日買超") + 3 * has("外資買超佔成交量") + 3 * has("外資連買")
        h = h_today.get(r["stock_id"], np.nan)
        up = bool(np.isfinite(h) and h >= 0.5)
        v4_chip = r["chip_score"] - old + 10 * up
        recs.append({"date": d, "sid": r["stock_id"], "ret": ret, "vol": r["avg_volume_20"],
                     "v3": r["total_score"], "v3_chip": r["chip_score"],
                     "v4": r["total_score"] - old + 10 * up, "v4_chip": v4_chip,
                     "hold_up": up, "has_hold": bool(np.isfinite(h))})

df = pd.DataFrame(recs)
df["ex"] = df["ret"] - df.groupby("date")["ret"].transform("mean")
df["period"] = np.where(df["date"] < "2025-11-01", "A 2025/02-10", "B 2025/11-2026/09")
print(f"\n樣本：{df['date'].nunique()} 天，{len(df):,} 筆（{df['date'].min()} ～ {df['date'].max()}）")
print(f"有外資持股 20 日變化的比例 {df['has_hold'].mean():.1%}，其中增 ≥0.5 個百分點 {df['hold_up'].mean():.1%}")

print("\n=== 每天分數最高 20 檔（同分依籌碼分、成交量）：平均 20 日超額報酬 % ===")
tops = {}
for v in ("v3", "v4"):
    top = df.sort_values([v, f"{v}_chip", "vol", "sid"], ascending=[False, False, False, True]) \
        .groupby("date").head(20)
    tops[v] = top
    by = top.groupby("period")["ex"].mean() * 100
    daily = top.groupby("date")["ex"].mean()
    print(f"  {v}: 全部 {top['ex'].mean() * 100:+.2f}｜" + "｜".join(f"{k} {x:+.2f}" for k, x in by.items())
          + f"｜贏過大盤的天數 {(daily > 0).mean():.0%}")
    by_year = top.groupby(top["date"].str[:4])["ex"].mean() * 100
    print("      各年：" + "｜".join(f"{k} {x:+.2f}" for k, x in by_year.items()))

overlap = pd.concat([tops["v3"].assign(v="v3"), tops["v4"].assign(v="v4")]) \
    .groupby(["date", "sid"])["v"].nunique()
print(f"\n前 20 名重疊比例：{(overlap == 2).groupby(level=0).sum().mean() / 20:.0%}")

print("\n=== 總分四分位：之後 20 日超額報酬 % ===")
for v in ("v3", "v4"):
    df["q"] = df.groupby("date")[v].transform(lambda x: pd.qcut(x.rank(method="first"), 4, labels=False))
    t = df.groupby(["period", "q"])["ex"].mean().unstack() * 100
    ic = df.groupby("date").apply(lambda g: g[v].rank().corr(g["ret"].rank()))
    print(f"\n[{v}] 每日排名相關 IC 平均 {ic.mean():+.3f}（IC>0 的天數 {(ic > 0).mean():.0%}）")
    print(t.round(2).to_string())
