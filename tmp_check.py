"""暫時檢查：新的參考價欄位抓到的事件，和資料庫現有的差多少（只讀，不寫資料庫）"""
import fetch_adjustments as adj
import fetch_stock_info as f

valid = f.get_valid_stock_ids()
new, problems = adj.fetch_range("2024-01-01", "2026-10-15", valid)
print("問題:", problems)
old = {}
start = 0
while True:
    rows = f.supabase.table("price_adjustments").select("*").range(start, start + 999).execute().data
    for r in rows:
        old[(r["stock_id"], r["date"], r["event_type"])] = r
    if len(rows) < 1000:
        break
    start += 1000
newk = {(r["stock_id"], r["date"], r["event_type"]): r for r in new}
print(f"資料庫 {len(old)} 筆，新抓 {len(newk)} 筆")
print("只在資料庫:", sorted(set(old) - set(newk))[:30])
print("只在新抓:", sorted(set(newk) - set(old))[:30])
changed = [(k, float(old[k]["factor"]), newk[k]["factor"]) for k in set(old) & set(newk)
           if abs(float(old[k]["factor"]) - newk[k]["factor"]) > 1e-6]
print(f"比例有變的 {len(changed)} 筆")
for k, a, b in sorted(changed, key=lambda x: x[0][1]):
    print(f"  {k[1]} {k[0]} {k[2]} {a:.4f} → {b:.4f}")
