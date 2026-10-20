"""暫時測試：期交所三大法人下載網址是否有當天資料（測完就刪）"""
import csv, io, requests
from datetime import date, timedelta

end = date.today()
start = end - timedelta(days=6)
url = "https://www.taifex.com.tw/cht/3/futContractsDateDown"
form = {"queryStartDate": start.strftime("%Y/%m/%d"), "queryEndDate": end.strftime("%Y/%m/%d"), "commodityId": "TXF"}
r = requests.post(url, data=form, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
print("status", r.status_code, r.headers.get("content-type"), len(r.content))
text = r.content.decode("big5", errors="replace")
rows = list(csv.reader(io.StringIO(text)))
for row in rows[:20]:
    print(row)

api = requests.get("https://openapi.taifex.com.tw/v1/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate", timeout=30)
api.encoding = "utf-8-sig"
for row in api.json():
    if row.get("ContractCode", "").strip() == "臺股期貨":
        print("openapi", row.get("Date"), row.get("Item"), row.get("OpenInterest(Long)"), row.get("OpenInterest(Short)"), row.get("OpenInterest(Net)"), row.get("ContractValueofOpenInterest(Net)(Thousands)"))
