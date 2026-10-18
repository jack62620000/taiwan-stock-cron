import json, requests
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
urls = [
 "https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS?date=20261016&selectType=ALLBUT0999&response=json",
 "https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS?date=20250102&selectType=ALLBUT0999&response=json",
 "https://www.tpex.org.tw/www/zh-tw/insti/qfii?date=2026/10/16&response=json",
 "https://www.tpex.org.tw/www/zh-tw/insti/qfii?date=2025/01/02&response=json",
 "https://www.tpex.org.tw/www/zh-tw/insti/qfiiStat?date=2026/10/16&response=json",
 "https://www.tpex.org.tw/web/stock/3insti/qfii/qfii_result.php?l=zh-tw&o=json&d=115/10/16",
 "https://www.tpex.org.tw/web/stock/3insti/qfii/qfii_result.php?l=zh-tw&o=json&d=114/01/02",
 "https://www.tpex.org.tw/openapi/v1/tpex_3insti_qfii",
]
for u in urls:
    print("=====", u)
    try:
        r = requests.get(u, headers=H, timeout=30)
        print(r.status_code, r.headers.get("content-type"))
        try:
            d = r.json()
        except Exception:
            print(r.text[:300]); continue
        if isinstance(d, list):
            print("list", len(d), json.dumps(d[:2], ensure_ascii=False)[:800]); continue
        print({k: (v if not isinstance(v, list) else f"list[{len(v)}]") for k, v in d.items() if k not in ("data",)} )
        if "fields" in d: print("fields", d["fields"])
        if "data" in d: print("n", len(d["data"]), d["data"][:2])
        if "tables" in d:
            for t in d["tables"]:
                print("table", t.get("title"), t.get("fields"), len(t.get("data", [])), t.get("data", [])[:2])
        if "aaData" in d: print("aa", len(d["aaData"]), d["aaData"][:2])
    except Exception as e:
        print("ERR", e)
