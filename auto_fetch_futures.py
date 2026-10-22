"""
期貨三大法人未平倉 - 獨立排程腳本
台灣時間晚上 9:08 執行，期交所此時已更新當日資料；隔天早上 6:30 再補抓一次

資料來源：優先用期交所網頁的「下載」網址（futContractsDateDown，當天收盤後就有資料，
一次抓最近幾天，順便補齊漏抓的日子）；抓不到或欄位對不上時，改用 openapi
（openapi 常比網頁晚一天以上才更新）。

不用「執行當下的日期」判斷是否為週末：GitHub 排程用 UTC 時間，而且常延遲數小時，
週五的補抓可能拖到週六才跑而被誤判跳過（2026-10-16 就因此漏抓）。
期交所回傳的每筆資料本身就帶交易日期，假日執行只會重存最近一個交易日，不會產生錯誤資料。
"""
import csv
import io
import requests
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import fetch_stock_info as f
from fetch_stock_info import parse_volume

DOWNLOAD_URL = "https://www.taifex.com.tw/cht/3/futContractsDateDown"
# 下載檔的欄位名稱 → 資料表欄位；期交所改版造成欄位對不上時，就改用 openapi
DOWNLOAD_COLUMNS = {
    "多方未平倉口數": "long_oi",
    "多方未平倉契約金額(千元)": "long_oi_value",
    "空方未平倉口數": "short_oi",
    "空方未平倉契約金額(千元)": "short_oi_value",
    "多空未平倉口數淨額": "net_oi",
    "多空未平倉契約金額淨額(千元)": "net_oi_value",
}


def parse_download_csv(text):
    """解析下載網址回傳的 CSV；格式不對時回傳 None"""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return None
    header = [h.strip() for h in rows[0]]
    need = ["日期", "商品名稱", "身份別", *DOWNLOAD_COLUMNS]
    if any(h not in header for h in need):
        return None
    idx = {h: header.index(h) for h in need}
    records = []
    for row in rows[1:]:
        if len(row) < len(header):
            continue
        name = row[idx["商品名稱"]].strip()
        if name != "臺股期貨":
            continue
        try:
            d = datetime.strptime(row[idx["日期"]].strip(), "%Y/%m/%d").date().isoformat()
        except ValueError:
            continue
        rec = {"date": d, "contract_code": name, "item": row[idx["身份別"]].strip()}
        for col, field in DOWNLOAD_COLUMNS.items():
            rec[field] = parse_volume(row[idx[col]])
        records.append(rec)
    return records


def fetch_from_download(days=7):
    """期交所網頁下載網址：抓最近幾天的臺股期貨三大法人。
    查詢範圍若包含期交所還沒公布的日子（例如今天收盤前）會回錯誤，所以失敗時改查到昨天為止。"""
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    start = today - timedelta(days=days)
    for end in (today, today - timedelta(days=1)):
        form = {"queryStartDate": start.strftime("%Y/%m/%d"), "queryEndDate": end.strftime("%Y/%m/%d"),
                "commodityId": "TXF"}
        try:
            r = requests.post(DOWNLOAD_URL, data=form, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
        except Exception as e:
            print(f"下載網址抓取失敗: {e}")
            return []
        records = parse_download_csv(r.content.decode("cp950", errors="replace"))
        if records:
            return records
        print(f"下載網址查到 {end} 沒有可用資料")
    return []


def fetch_from_openapi():
    url = "https://openapi.taifex.com.tw/v1/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate"
    try:
        r = requests.get(url, timeout=10)
        r.encoding = "utf-8-sig"  # ✅ 避免 BOM 錯誤
        data = r.json()
    except Exception as e:
        print(f"抓取失敗: {e}")
        return []

    records = []
    for row in data:
        if row.get("ContractCode", "").strip() != "臺股期貨":
            continue
        date_str = row.get("Date", "").strip()
        if len(date_str) != 8:
            continue
        record_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        records.append({
            "date":           record_date,
            "contract_code":  row.get("ContractCode", "").strip(),
            "item":           row.get("Item", "").strip(),
            "long_oi":        parse_volume(row.get("OpenInterest(Long)", "")),
            "long_oi_value":  parse_volume(row.get("ContractValueofOpenInterest(Long)(Thousands)", "")),
            "short_oi":       parse_volume(row.get("OpenInterest(Short)", "")),
            "short_oi_value": parse_volume(row.get("ContractValueofOpenInterest(Short)(Thousands)", "")),
            "net_oi":         parse_volume(row.get("OpenInterest(Net)", "")),
            "net_oi_value":   parse_volume(row.get("ContractValueofOpenInterest(Net)(Thousands)", "")),
        })
    return records


def fetch_futures_institutional():
    print("正在下載期貨三大法人未平倉（期交所下載網址）...")
    records = fetch_from_download()
    if records:
        return records
    print("改用 openapi 抓取...")
    return fetch_from_openapi()

if __name__ == "__main__":
    today = date.today().isoformat()
    print(f"========== 期貨排程執行：{today} ==========")

    futures_data = fetch_futures_institutional()
    if futures_data:
        dates = sorted({r["date"] for r in futures_data})
        print(f"期交所資料日期：{dates[0]} ~ {dates[-1]}（共 {len(dates)} 天）")
    if not futures_data:
        print("❌ 下載網址和 openapi 都沒有取得資料")
        sys.exit(1)
    _, failed = f.save_data_batches("futures_institutional", futures_data)
    if failed:
        print(f"❌ 有 {failed} 筆寫入失敗")
        sys.exit(1)

    print("\n✅ 期貨排程執行完畢！")
