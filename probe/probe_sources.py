"""暫時用：確認官方開放資料欄位格式（開 PR 前會刪掉）"""
import json, time, requests
for u in ("https://openapi.twse.com.tw/v1/opendata/t187ap47_L",
          "https://www.tpex.org.tw/openapi/v1/tpex_spendi_today",
          "https://www.tpex.org.tw/openapi/v1/tpex_spendi_history",
          "https://openapi.twse.com.tw/v1/exchangeReport/TWTAWU"):
    time.sleep(3)
    try:
        r = requests.get(u, timeout=30)
        r.encoding = "utf-8"
        d = r.json()
        print(f"\n### {u} -> {r.status_code} 筆數 {len(d)}")
        print(json.dumps(d[:3], ensure_ascii=False, indent=1))
        if "t187ap47" in u:
            etf = [x for x in d if str(next(iter(x.values()), "")).startswith("00") or "指數股票型" in json.dumps(x, ensure_ascii=False)]
            print("ETF 相關筆數", len(etf)); print(json.dumps(etf[:2], ensure_ascii=False, indent=1))
        if "history" in u:
            print(json.dumps([x for x in d if str(x.get("SecuritiesCompanyCode", x.get("Code", ""))).startswith("00")][:5], ensure_ascii=False))
    except Exception as e:
        print(f"\n### {u} -> ERR {e}")
