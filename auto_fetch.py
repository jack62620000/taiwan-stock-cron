"""
自動化版本 - 給 GitHub Actions 排程使用
每次執行會自動同步「今天」的股價、三大法人、融資融券
"""
import requests
from supabase import create_client
from dotenv import load_dotenv
import os
import time
from datetime import datetime

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# ===== 從 fetch_stock_info.py 複製過來的工具函式 =====

def parse_price(val_str):
    if not val_str: return None
    val_str = str(val_str).replace(",", "").strip()
    if val_str in ["---", "除權", "除息", "停牌", "破產", "", "X"]: return None
    try: return float(val_str)
    except ValueError: return None

def parse_volume(val_str):
    if not val_str: return 0
    val_str = str(val_str).replace(",", "").strip()
    try: return int(val_str)
    except ValueError: return 0

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

# ===== 期貨三大法人未平倉 =====
def fetch_futures_institutional():
    url = "https://openapi.taifex.com.tw/v1/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate"
    print("正在下載期貨三大法人未平倉...")
    try:
        r = requests.get(url, timeout=10)
        r.encoding = "utf-8"
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
        date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        records.append({
            "date": date,
            "contract_code": row.get("ContractCode", "").strip(),
            "item": row.get("Item", "").strip(),
            "long_oi": parse_volume(row.get("OpenInterest(Long)", "")),
            "long_oi_value": parse_volume(row.get("ContractValueofOpenInterest(Long)(Thousands)", "")),
            "short_oi": parse_volume(row.get("OpenInterest(Short)", "")),
            "short_oi_value": parse_volume(row.get("ContractValueofOpenInterest(Short)(Thousands)", "")),
            "net_oi": parse_volume(row.get("OpenInterest(Net)", "")),
            "net_oi_value": parse_volume(row.get("ContractValueofOpenInterest(Net)(Thousands)", "")),
        })
    return records
    
# ===== 每日股價 =====
def fetch_twse_daily_all(today_str, valid_stocks):
    url_date = today_str.replace("-", "")
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={url_date}&type=ALLBUT0999&response=json"
    print(f"正在從證交所下載 {today_str} 上市行情...")
    try:
        r = requests.get(url, timeout=15)
        data = r.json()
        if data.get("stat") != "OK":
            print(f"證交所提示: {data.get('stat')}"); return []
        price_table = next(
            (t for t in data.get("tables", []) if "每日收盤行情" in t.get("title", "")), None
        )
        if not price_table:
            print("找不到每日收盤行情表"); return []
        records = []
        for row in price_table.get("data", []):
            stock_id = row[0].strip()
            if stock_id not in valid_stocks: continue
            records.append({
                "stock_id": stock_id, "date": today_str,
                "open_price": parse_price(row[5]), "high_price": parse_price(row[6]),
                "low_price": parse_price(row[7]), "close_price": parse_price(row[8]),
                "volume": parse_volume(row[2]),
            })
        return records
    except Exception as e:
        print(f"抓取上市行情失敗: {e}"); return []

def fetch_tpex_otc_daily(today_str, valid_stocks):
    dt = datetime.strptime(today_str, "%Y-%m-%d")
    url_date = f"{dt.year}%2F{dt.strftime('%m')}%2F{dt.strftime('%d')}"
    url = f"https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={url_date}&type=AL&id=&response=json&order=0&sort=asc"
    print(f"正在從櫃買中心下載 {today_str} 上櫃行情...")
    try:
        r = requests.get(url, timeout=30)
        data = r.json()
        tables = data.get("tables", [])
        if not tables or not tables[0].get("data"):
            print("上櫃股價今日尚未更新或查無資料"); return []
        records = []
        for row in tables[0]["data"]:
            stock_id = row[0].strip()
            if stock_id not in valid_stocks: continue
            records.append({
                "stock_id": stock_id, "date": today_str,
                "open_price": parse_price(row[4]), "high_price": parse_price(row[5]),
                "low_price": parse_price(row[6]), "close_price": parse_price(row[2]),
                "volume": parse_volume(row[7]),
            })
        return records
    except Exception as e:
        print(f"抓取上櫃行情失敗: {e}"); return []

# ===== 三大法人 =====
def fetch_twse_stock_ins(today_str, valid_stocks):
    url_date = today_str.replace("-", "")
    url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={url_date}&selectType=ALLBUT0999&response=json"
    print(f"正在從證交所下載 {today_str} 上市法人...")
    try:
        r = requests.get(url, timeout=15)
        data = r.json()
        if data.get("stat") != "OK":
            print(f"上市法人尚未更新: {data.get('stat')}"); return []
        records = []
        for row in data.get("data", []):
            if len(row) < 19: continue
            stock_id = row[0].strip()
            if stock_id not in valid_stocks: continue
            records.append({
                "stock_id": stock_id, "date": today_str,
                "foreign_net_shares": parse_volume(row[4]),
                "sitca_net_shares": parse_volume(row[10]),
                "dealers_net_shares": parse_volume(row[11]),
                "total_net_shares": parse_volume(row[18]),
            })
        return records
    except Exception as e:
        print(f"抓取上市法人失敗: {e}"); return []

def fetch_tpex_stock_ins(today_str, valid_stocks):
    url_date = today_str.replace("-", "")
    url = f"https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date={url_date}&id=&response=json"
    print(f"正在從櫃買中心下載 {today_str} 上櫃法人...")
    try:
        r = requests.get(url, timeout=15)
        data = r.json()
        tables = data.get("tables", [])
        if not tables or not tables[0].get("data"):
            print("上櫃法人今日尚未更新或查無資料"); return []
        records = []
        for row in tables[0]["data"]:
            stock_id = row[0].strip()
            if stock_id not in valid_stocks: continue
            records.append({
                "stock_id": stock_id, "date": today_str,
                "foreign_net_shares": parse_volume(row[4]),
                "sitca_net_shares": parse_volume(row[10]),
                "dealers_net_shares": parse_volume(row[22]),
                "total_net_shares": parse_volume(row[23]),
            })
        return records
    except Exception as e:
        print(f"抓取上櫃法人失敗: {e}"); return []

# ===== 融資融券 =====
def fetch_twse_margin(today_str, valid_stocks):
    url_date = today_str.replace("-", "")
    url = f"https://www.twse.com.tw/exchangeReport/MI_MARGN?response=json&date={url_date}&selectType=ALL"
    print(f"正在從證交所下載 {today_str} 上市融資融券...")
    try:
        r = requests.get(url, timeout=15)
        data = r.json()
        if data.get("stat") != "OK":
            print(f"上市融資融券尚未更新: {data.get('stat')}"); return []
        table = next(
            (t for t in data.get("tables", []) if "融資融券彙總" in t.get("title", "")), None
        )
        if not table:
            print("找不到融資融券彙總表"); return []
        records = []
        for row in table.get("data", []):
            stock_id = row[0].strip()
            if stock_id not in valid_stocks: continue
            records.append({
                "stock_id": stock_id, "date": today_str,
                "margin_balance": parse_volume(row[6]),
                "short_balance": parse_volume(row[12]),
            })
        return records
    except Exception as e:
        print(f"抓取上市融資融券失敗: {e}"); return []

def fetch_tpex_margin(today_str, valid_stocks):
    dt = datetime.strptime(today_str, "%Y-%m-%d")
    url_date = f"{dt.year}%2F{dt.strftime('%m')}%2F{dt.strftime('%d')}"
    url = f"https://www.tpex.org.tw/www/zh-tw/margin/sbl?date={url_date}&id=&response=json"
    print(f"正在從櫃買中心下載 {today_str} 上櫃融資融券...")
    try:
        r = requests.get(url, timeout=15)
        data = r.json()
        tables = data.get("tables", [])
        if not tables or not tables[0].get("data"):
            print("上櫃融資融券今日尚未更新或查無資料"); return []
        records = []
        for row in tables[0]["data"]:
            stock_id = row[0].strip()
            if stock_id not in valid_stocks: continue
            records.append({
                "stock_id": stock_id, "date": today_str,
                "margin_balance": None,
                "short_balance": parse_volume(row[6]),
            })
        return records
    except Exception as e:
        print(f"抓取上櫃融資融券失敗: {e}"); return []
# ===== 大戶持股比例 =====
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
        
# ===== 主程式（無互動，自動執行）=====
if __name__ == "__main__":
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"========== 自動排程執行：{today} ==========")

    valid_stocks = get_valid_stock_ids()
    print(f"資料庫內現有 {len(valid_stocks)} 檔股票可供對應。")

    SLEEP_BETWEEN_API = 2

    print("\n--- 同步每日股價 ---")
    all_prices = []
    all_prices.extend(fetch_twse_daily_all(today, valid_stocks))
    time.sleep(SLEEP_BETWEEN_API)
    all_prices.extend(fetch_tpex_otc_daily(today, valid_stocks))
    save_data_batches("daily_prices", all_prices)
    time.sleep(SLEEP_BETWEEN_API)

    print("\n--- 同步三大法人 ---")
    all_ins = []
    all_ins.extend(fetch_twse_stock_ins(today, valid_stocks))
    time.sleep(SLEEP_BETWEEN_API)
    all_ins.extend(fetch_tpex_stock_ins(today, valid_stocks))
    save_data_batches("stock_ins_daily", all_ins)
    time.sleep(SLEEP_BETWEEN_API)

    print("\n--- 同步融資融券 ---")
    all_margin = []
    all_margin.extend(fetch_twse_margin(today, valid_stocks))
    time.sleep(SLEEP_BETWEEN_API)
    all_margin.extend(fetch_tpex_margin(today, valid_stocks))
    save_data_batches("margin_trading", all_margin)
    time.sleep(SLEEP_BETWEEN_API)

    print("\n--- 同步期貨三大法人未平倉 ---")
    futures_data = fetch_futures_institutional()
    save_data_batches("futures_institutional", futures_data)
    time.sleep(SLEEP_BETWEEN_API)

    # 大戶持股只在週六排程時執行（由環境變數 RUN_HOLDER 控制）
    if os.getenv("RUN_HOLDER") == "true":
        print("\n--- 同步大戶持股比例 ---")
        holder_data = fetch_holder_distribution(valid_stocks)
        save_data_batches("holder_distribution", holder_data)

    print("\n✅ 自動排程執行完畢！")
