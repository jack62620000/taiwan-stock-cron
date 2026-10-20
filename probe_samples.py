"""暫時：抓證交所、櫃買中心真實回應，精簡後印出當測試樣本（測完就刪）"""
import json, requests
import fetch_stock_info as f
D = "2026-10-19"
URLS = {
    "twse_price": f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20261019&type=ALLBUT0999&response=json",
    "tpex_price": f"https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={f.tpex_url_date(D)}&type=AL&id=&response=json&order=0&sort=asc",
    "twse_ins": "https://www.twse.com.tw/rwd/zh/fund/T86?date=20261019&selectType=ALLBUT0999&response=json",
    "tpex_ins": f"https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date={f.tpex_url_date(D)}&id=&response=json",
    "twse_margin": "https://www.twse.com.tw/exchangeReport/MI_MARGN?response=json&date=20261019&selectType=ALL",
    "tpex_margin": f"https://www.tpex.org.tw/www/zh-tw/margin/balance?date={f.tpex_url_date(D)}&id=&response=json",
}
KEEP = {"2330", "2317", "1101", "6488", "3105", "8069"}
def trim_rows(rows):
    return [r for r in rows if isinstance(r, list) and r and str(r[0]).strip() in KEEP]
for name, url in URLS.items():
    try:
        data = f.get_json(url, timeout=60, retries=4, wait=10)
    except Exception as e:
        print("@@" + name + "@@ERROR " + repr(e)); continue
    if "data" in data:
        data["data"] = trim_rows(data["data"])
    for t in data.get("tables", []):
        if "data" in t:
            t["data"] = trim_rows(t["data"])[:6] if trim_rows(t["data"]) else t["data"][:2]
    print("@@" + name + "@@" + json.dumps(data, ensure_ascii=False))
