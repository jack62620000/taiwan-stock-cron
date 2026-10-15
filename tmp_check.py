"""暫時檢查：回補資料是否漏掉已下市股票、可疑的除權事件原始欄位（只讀，不寫資料庫）"""
import json, re, time
import fetch_stock_info as f

valid = f.get_valid_stock_ids()
print("stock_info 檔數", len(valid))

for d in ["2024-01-02", "2024-07-01", "2025-01-02", "2025-08-29"]:
    tw = f.get_json(f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d.replace('-','')}&type=ALLBUT0999&response=json")
    t = next(t for t in tw["tables"] if "每日收盤行情" in t.get("title", ""))
    tw_rows = [(r[0].strip(), r[1].strip()) for r in t["data"]]
    time.sleep(3)
    tp = f.get_json(f"https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={f.tpex_url_date(d)}&type=AL&id=&response=json&order=0&sort=asc")
    tp_rows = [(r[0].strip(), r[1].strip()) for r in tp["tables"][0]["data"]]
    time.sleep(3)
    for label, rows in (("上市", tw_rows), ("上櫃", tp_rows)):
        miss = [(c, n) for c, n in rows if c not in valid]
        stocks = [(c, n) for c, n in miss if re.fullmatch(r"\d{4}", c)]
        kept = sum(c in valid for c, _ in rows)
        print(f"\n{d} {label}: 來源 {len(rows)} 檔，已收 {kept}，未收 {len(miss)}，其中四碼普通股 {len(stocks)}")
        print("  四碼未收:", stocks[:60])
        other = sorted({re.sub(r"\d", "9", c) for c, _ in miss if not re.fullmatch(r"\d{4}", c)})
        print("  其他未收代號樣式:", other[:30])

print("\n===== 可疑除權事件原始資料 =====")
sus = [("2736", "2024-05-28", "tpex"), ("2429", "2024-07-02", "twse"), ("4991", "2025-12-22", "tpex"),
       ("7610", "2026-07-13", "twse"), ("6225", "2026-08-18", "twse"), ("2464", "2026-09-02", "twse"),
       ("6834", "2026-10-02", "twse")]
for sid, d, src in sus:
    ymd = d.replace("-", "")
    if src == "twse":
        data = f.get_json(f"https://www.twse.com.tw/rwd/zh/exRight/TWT49U?startDate={ymd}&endDate={ymd}&response=json")
        print(f"\n{sid} {d} 證交所 欄位:", data.get("fields"))
        for r in data.get("data", []):
            if r[1].strip() == sid:
                print("  ", r)
    else:
        data = f.get_json(f"https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?startDate={d.replace('-','/')}&endDate={d.replace('-','/')}&response=json")
        for t in data.get("tables", []):
            print(f"\n{sid} {d} 櫃買 欄位:", t.get("fields"))
            for r in t.get("data", []):
                if str(r[1]).strip() == sid:
                    print("  ", r)
    time.sleep(3)
