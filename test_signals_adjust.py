"""暫時測試：每日選股評分改用還原股價前後的差異（只讀不寫），測完會刪除"""
import pandas as pd
import signals as s

sb = s._client()
targets, data = s.load_inputs(sb, "2026-09-01", None)
new = s.compute_signals(targets, data)
old = s.compute_signals(targets, {**data, "adjust": pd.DataFrame()})
changed = 0
for d in targets:
    a = {r["stock_id"]: r for r in new.get(d, [])}
    b = {r["stock_id"]: r for r in old.get(d, [])}
    diff = [sid for sid in a if sid in b and (a[sid]["total_score"], a[sid]["change_20d"]) != (b[sid]["total_score"], b[sid]["change_20d"])]
    assert all(a[sid]["close_price"] == b[sid]["close_price"] for sid in a if sid in b)
    changed += len(diff)
    top_a = [r["stock_id"] for r in sorted((r for r in a.values() if r["rank"]), key=lambda r: r["rank"])[:20]]
    top_b = [r["stock_id"] for r in sorted((r for r in b.values() if r["rank"]), key=lambda r: r["rank"])[:20]]
    print(f"{d}：{len(a)} 檔，分數或 20 日漲幅有變 {len(diff)} 檔，前 20 名變動 {len(set(top_a) ^ set(top_b)) // 2} 檔",
          "例：" + "、".join(f"{sid} {b[sid]['change_20d']}%→{a[sid]['change_20d']}% 分 {b[sid]['total_score']}→{a[sid]['total_score']}" for sid in diff[:3]))
print("共", changed, "筆不同；收盤價全部相同")
