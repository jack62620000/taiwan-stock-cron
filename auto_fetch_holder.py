"""
週六專用 - 只同步大戶持股比例
"""
import requests
from supabase import create_client
from dotenv import load_dotenv
import os

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

def get_valid_stock_ids():
    valid_stocks = set()
    start = 0
    page_size = 1000
    while True:
        try:
            res = supabase.table("stock_info").select("stock_id").range(start, start + page_size - 1).execute()
            data = res.data
            if not data: break
            for item in data: valid_stocks.add(item["stock_id"])
            if len(data) < page_size: break
            start += page_size
        except Exception as e:
            print(f"無法獲取股票清單: {e}"); break
    return valid_stocks

def save_data_batches(table_name, records):
    if not records: return
    batch_size = 500
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        try: supabase.table(table_name).upsert(batch).execute()
        except Exception as e: print(f"儲存 {table_name} 錯誤 (Index {i}): {e}")
    print(f"成功儲存/更新共 {len(records)} 筆資料至 {table_name}！")

def fetch_holder_distribution(valid_stocks):
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    url = "https://smart.tdcc.com.tw/opendata/getOD.ashx?id=1-5"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    print("正在從集保結算所下載大戶持股資料...")
    try:
        import pandas as pd
        from io import StringIO
        r = requests.get(url, headers=headers, timeout=30, verify=False)
        r.encoding = "utf-8"
        df = pd.read_csv(StringIO(r.text))

        df = df[df["持股分級"].isin([12, 13, 14, 15])]
        df = df[df["證券代號"].astype(str).str.strip().isin(valid_stocks)]

        def parse_week_date(d):
            d = str(d).strip()
            if len(d) == 8 and d.isdigit():
                return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            return None

        result = (
            df.groupby(["資料日期", "證券代號"])["占集保庫存數比例%"]
            .sum()
            .reset_index()
        )

        records = []
        for _, row in result.iterrows():
            records.append({
                "stock_id": str(row["證券代號"]).strip(),
                "week_date": parse_week_date(str(row["資料日期"]).strip()),
                "big_holder_ratio": round(float(row["占集保庫存數比例%"]), 2),
            })
        return records
    except Exception as e:
        print(f"抓取大戶持股失敗: {e}")
        return []

if __name__ == "__main__":
    print("========== 週六排程：同步大戶持股比例 ==========")
    valid_stocks = get_valid_stock_ids()
    print(f"資料庫內現有 {len(valid_stocks)} 檔股票可供對應。")

    holder_data = fetch_holder_distribution(valid_stocks)
    save_data_batches("holder_distribution", holder_data)

    print("\n✅ 大戶持股同步完成！")
