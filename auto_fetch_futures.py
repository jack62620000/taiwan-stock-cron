"""
期貨三大法人未平倉 - 獨立排程腳本
台灣時間晚上 9:08 執行，期交所此時已更新當日資料；隔天早上 6:30 再補抓一次

不用「執行當下的日期」判斷是否為週末：GitHub 排程用 UTC 時間，而且常延遲數小時，
週五的補抓可能拖到週六才跑而被誤判跳過（2026-10-16 就因此漏抓）。
期交所回傳的每筆資料本身就帶交易日期，假日執行只會重存最近一個交易日，不會產生錯誤資料。
"""
import requests
from supabase import create_client
from dotenv import load_dotenv
import os
from datetime import date

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

def parse_volume(val_str):
    if not val_str: return 0
    val_str = str(val_str).replace(",", "").strip()
    try: return int(val_str)
    except ValueError: return 0

def save_data_batches(table_name, records):
    if not records:
        print("無資料可儲存")
        return
    batch_size = 500
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        try: supabase.table(table_name).upsert(batch).execute()
        except Exception as e: print(f"儲存 {table_name} 錯誤 (Index {i}): {e}")
    print(f"成功儲存/更新共 {len(records)} 筆資料至 {table_name}！")

def fetch_futures_institutional():
    url = "https://openapi.taifex.com.tw/v1/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate"
    print("正在下載期貨三大法人未平倉...")
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

if __name__ == "__main__":
    today = date.today().isoformat()
    print(f"========== 期貨排程執行：{today} ==========")

    futures_data = fetch_futures_institutional()
    if futures_data:
        print(f"期交所資料日期：{futures_data[0]['date']}")
    save_data_batches("futures_institutional", futures_data)

    print("\n✅ 期貨排程執行完畢！")
