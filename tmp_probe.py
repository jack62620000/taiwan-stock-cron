"""暫時：比對觀測站彙總表與資料庫現有月營收（只讀）"""
import re
import fetch_revenue_history as h
import fetch_stock_info as f

html = h._download(h.URL.format(market="sii", roc_year=114, month=1, kind="0"))
m = re.search(r"<tr[^>]*>\s*<td[^>]*>\s*1101\s*</td>.*?</tr>", html, re.S)
print("原始列：", m.group(0)[:1500])

valid = f.get_valid_stock_ids()
for y, mo in ((2026, 4), (2026, 8)):
    ym = f"{y}-{mo:02d}"
    recs, probs = h.fetch_month(y, mo, valid)
    print(ym, "抓到", len(recs), "問題", probs)
    db = {}
    start = 0
    while True:
        res = f.supabase.table("monthly_revenue").select("stock_id,revenue,yoy_growth,mom_growth,cumulative_revenue").eq("year_month", ym).range(start, start + 999).execute()
        for r in res.data:
            db[r["stock_id"]] = r
        if len(res.data) < 1000:
            break
        start += 1000
    both = [r for r in recs if r["stock_id"] in db]
    rev_diff = [r["stock_id"] for r in both if r["revenue"] != db[r["stock_id"]]["revenue"]]
    cum_diff = [r["stock_id"] for r in both if r["cumulative_revenue"] != db[r["stock_id"]]["cumulative_revenue"]]
    yoy_diff = [r["stock_id"] for r in both if r["yoy_growth"] is not None and db[r["stock_id"]]["yoy_growth"] is not None and abs(r["yoy_growth"] - float(db[r["stock_id"]]["yoy_growth"])) > 0.02]
    print(f"  資料庫 {len(db)} 檔，兩邊都有 {len(both)}；營收不同 {len(rev_diff)} {rev_diff[:5]}；累計不同 {len(cum_diff)} {cum_diff[:5]}；年增率差>0.02 {len(yoy_diff)} {yoy_diff[:5]}")
    only_db = sorted(set(db) - {r['stock_id'] for r in recs})
    print(f"  只有資料庫有：{len(only_db)} {only_db[:10]}")
    for sid in (rev_diff + yoy_diff)[:3]:
        print("   ", sid, [r for r in recs if r["stock_id"] == sid][0], db[sid])
