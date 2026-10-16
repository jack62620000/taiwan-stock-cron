"""暫時測試：千張大戶歷史資料來源（只抓資料、不寫資料庫）"""
import re, time, requests
import urllib3
urllib3.disable_warnings()
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

print("=== 集保 qryStock ===")
s = requests.Session(); s.headers.update(H)
url = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"
r = s.get(url, timeout=30, verify=False)
print("GET", r.status_code, len(r.text))
tok = re.search(r'name="SYNCHRONIZER_TOKEN"[^>]*value="([^"]+)"', r.text) or re.search(r'value="([^"]+)"[^>]*name="SYNCHRONIZER_TOKEN"', r.text)
dates = re.findall(r'<option value="(\d{8})"', r.text)
print("token", bool(tok), "dates", len(dates), dates[:3], dates[-3:])
for m in re.finditer(r'<input[^>]+>', r.text):
    if 'hidden' in m.group(0): print("  hidden:", m.group(0)[:200])
def q(date, sid):
    global tok
    data = {"SYNCHRONIZER_TOKEN": tok.group(1) if tok else "", "SYNCHRONIZER_URI": "/portal/zh/smWeb/qryStock",
            "method": "submit", "firDate": date, "scaDate": date, "sqlMethod": "StockNo",
            "stockNo": sid, "stockName": ""}
    rr = s.post(url, data=data, timeout=20, verify=False)
    t = re.search(r'name="SYNCHRONIZER_TOKEN"[^>]*value="([^"]+)"', rr.text)
    if t: tok = t
    rows = re.findall(r'<tr>\s*<td[^>]*>(\d+)</td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([\d,]+)</td>\s*<td[^>]*>([\d,]+)</td>\s*<td[^>]*>([\d.]+)</td>', rr.text)
    return rr.status_code, rows, rr.text
if dates:
    for d in [dates[0], dates[-1]]:
        st, rows, txt = q(d, "2330")
        big = sum(float(x[4]) for x in rows if x[0] in ("12","13","14","15"))
        print(d, st, len(rows), "大戶%", round(big, 2), rows[:2])
        if not rows:
            i = txt.find("持股分級"); print(txt[i-200:i+1500])
    import random
    ids = ["2317","2454","6488","8069","3008","2603","1101","5347","6274","2881","2002","1301","3711","2382","6669","3034","2412","5274","3529","8299"]
    for gap in (1.0, 0.3):
        t0 = time.time(); ok = fail = 0
        for sid in ids:
            for attempt in range(3):
                try:
                    st, rows, _ = q(random.choice(dates), sid); ok += bool(rows); break
                except Exception as e:
                    fail += 1; print("  逾時/錯誤", sid, type(e).__name__); time.sleep(5)
            time.sleep(gap)
        print(f"間隔 {gap} 秒：20 次查詢 {time.time()-t0:.1f} 秒，成功 {ok}，錯誤 {fail}")
print("=== FinMind ===")
for ds in ["TaiwanStockHoldingSharesPer"]:
    r = requests.get("https://api.finmindtrade.com/api/v4/data", params={"dataset": ds, "data_id": "2330", "start_date": "2024-01-01"}, timeout=60)
    j = r.json(); d = j.get("data", [])
    print(ds, r.status_code, j.get("msg"), len(d), d[:1], d[-1:] if d else "")
    r = requests.get("https://api.finmindtrade.com/api/v4/data", params={"dataset": ds, "start_date": "2024-01-05", "end_date": "2024-01-05"}, timeout=60)
    j = r.json(); print("全市場單日", r.status_code, j.get("msg"), len(j.get("data", [])))
