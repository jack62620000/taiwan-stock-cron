"""暫時測試：期交所三大法人下載網址是否有當天資料（測完就刪）"""
import csv, io, requests
from datetime import date, timedelta

H = {"User-Agent": "Mozilla/5.0"}
url = "https://www.taifex.com.tw/cht/3/futContractsDateDown"
today = date.today()
for s, e in [(today - timedelta(days=6), today - timedelta(days=1)), (date(2026, 10, 16), date(2026, 10, 19)), (today, today)]:
    for fmt in ("%Y/%m/%d",):
        form = {"queryStartDate": s.strftime(fmt), "queryEndDate": e.strftime(fmt), "commodityId": "TXF"}
        try:
            r = requests.post(url, data=form, timeout=30, headers=H)
        except Exception as ex:
            print("ERR", form, ex); continue
        text = r.content.decode("big5", errors="replace")
        print("==", form, r.status_code, r.headers.get("content-type"), len(r.content))
        if "DateTime error" in r.content.decode("utf-8", errors="replace"):
            print("  DateTime error"); continue
        for row in list(csv.reader(io.StringIO(text)))[:12]:
            print(" ", row)

api = requests.get("https://openapi.taifex.com.tw/v1/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate", timeout=30, headers=H)
print("openapi status", api.status_code, api.headers.get("content-type"), len(api.content), api.content[:200])
try:
    api.encoding = "utf-8-sig"
    for row in api.json():
        if row.get("ContractCode", "").strip() == "臺股期貨":
            print("openapi", row)
except Exception as ex:
    print("openapi json fail", ex)
