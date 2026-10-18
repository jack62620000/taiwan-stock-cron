"""
台股資料爬蟲（修正版）— 取代儲存庫中的 fetch_stock_info.py
直接執行是互動模式；auto_fetch.py 也會匯入這個檔案的抓取函式，兩邊共用同一套邏輯。

相較原版的修改：
  [A] 產業別對照表改為官方代碼（上市、上櫃共用）
  [B] 上櫃三大法人：日期改用 YYYY%2FMM%2FDD，投信改讀 row[13]，並核對回應日期
  [C] 融資融券：上市、上櫃統一為「張」；上櫃改用 /margin/balance（含融資、限額、使用率）
      功能 4 寫入 margin_utilization（含融資、融券餘額），可用區間模式回補；功能 10 併入功能 4
  [D] 所有櫃買中心帶日期的請求都核對回應日期，不符就不寫入
  [E] 假日清單改用民國日期解析，並排除「開始交易日／最後交易日」
  [F] save_data_batches：過濾主鍵為空與重複的資料，並如實回報成功／失敗筆數
  [G] 每次執行自動更新 stock_info，新上市櫃與轉換市場的股票才會被納入
  [H] 季報：缺少年度或季別的資料不寫入
  [I] 每日股價、法人、融資券的請求在連線中斷時自動重試（最多 3 次）
  [J] 季報新增營收、毛利、營業利益與毛利率、營業利益率（需先在資料庫加欄位）
  [K] 月營收與季報存下來源的其餘有用欄位，並計算負債比、流動比率（需先在資料庫加欄位）
"""
import requests
from supabase import create_client
from dotenv import load_dotenv
import os
import time
from datetime import date, datetime, timedelta

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# [A] 上市、上櫃共用的官方產業別代碼
INDUSTRY_MAP = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維",
    "05": "電機機械", "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙工業",
    "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業", "14": "建材營造",
    "15": "航運業", "16": "觀光餐旅", "17": "金融保險", "18": "貿易百貨",
    "19": "綜合", "20": "其他", "21": "化學工業", "22": "生技醫療業",
    "23": "油電燃氣業", "24": "半導體業", "25": "電腦及週邊設備業",
    "26": "光電業", "27": "通信網路業", "28": "電子零組件業",
    "29": "電子通路業", "30": "資訊服務業", "31": "其他電子業",
    "32": "文化創意業", "33": "農業科技業", "34": "電子商務",
    "35": "綠能環保", "36": "數位雲端", "37": "運動休閒", "38": "居家生活",
    "80": "管理股票", "91": "存託憑證",
}

MARKET_MAP = {"listed": "上市", "otc": "上櫃"}

# [F] 各資料表的主鍵，用來在寫入前過濾不完整的資料
TABLE_KEYS = {
    "daily_prices":          ("stock_id", "date"),
    "stock_ins_daily":       ("stock_id", "date"),
    "margin_utilization":    ("stock_id", "date"),
    "holder_distribution":   ("stock_id", "week_date"),
    "monthly_revenue":       ("stock_id", "year_month"),
    "quarterly_report":      ("stock_id", "year", "quarter"),
    "futures_institutional": ("date", "contract_code", "item"),
    "price_adjustments":     ("stock_id", "date", "event_type"),
}


def is_valid_stock(stock_id):
    sid = stock_id.strip()
    return sid.isdigit() and (len(sid) == 4 or (len(sid) == 6 and sid.startswith("00")))

def parse_date(date_str):
    """解析集保日期格式 20260612 -> 2026-06-12"""
    try:
        d = str(date_str).strip()
        if len(d) == 8 and d.isdigit():
            return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        return None
    except:
        return None

def parse_roc_date(date_str):
    """解析民國年日期格式 1150623 -> 2026-06-23"""
    try:
        d = str(date_str).strip()
        if len(d) == 7 and d.isdigit():
            roc_year = int(d[:3])
            month = d[3:5]
            day = d[5:7]
            return f"{roc_year + 1911}-{month}-{day}"
        return None
    except:
        return None

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

def parse_float(val):
    if not val:
        return None
    try:
        return float(str(val).replace(",", "").strip())
    except ValueError:
        return None

def first_value(row, keys):
    """依序取第一個有值的欄位並轉成數字；都沒有就回傳 None。
    各類公司（一般業、金控、保險、證券）對同一個項目的欄位名稱不完全相同。"""
    for key in keys:
        value = parse_float(row.get(key, ""))
        if value is not None:
            return value
    return None

def calc_rate(balance, limit):
    return round(balance / limit * 100, 2) if limit and limit > 0 else None


# ==================== 網路請求共用工具 ====================
def get_json(url, timeout=15, retries=3, wait=5):
    """
    [I] GET 並解析 JSON。連線中斷或逾時（例如 IncompleteRead）會自動重試；
    回應不是 JSON（例如被來源擋下時的錯誤頁）則不重試，直接交給呼叫端處理。
    """
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, timeout=timeout)
            break
        except requests.exceptions.RequestException as e:
            if attempt == retries:
                raise
            print(f"  第 {attempt} 次請求失敗（{type(e).__name__}），{wait} 秒後重試...")
            time.sleep(wait)
    return r.json()


# ==================== 櫃買中心共用工具 ====================
def tpex_url_date(today_str):
    """2026-10-06 -> 2026%2F10%2F06。櫃買中心只認這個格式，其他格式會被忽略。"""
    dt = datetime.strptime(today_str, "%Y-%m-%d")
    return f"{dt.year}%2F{dt.strftime('%m')}%2F{dt.strftime('%d')}"

def tpex_date_matches(data, today_str, label):
    """[D] 櫃買中心看不懂日期時會回傳最新一天的資料，所以一定要核對回應日期。"""
    resp_date = str(data.get("date", "")).strip()
    expected = today_str.replace("-", "")
    if not resp_date:
        print(f"⚠️ {label}：回應中沒有日期欄位，無法核對，請人工確認")
        return True
    if resp_date != expected:
        print(f"⚠️ {label}：回應日期 {resp_date} 與要求日期 {expected} 不符，跳過不寫入")
        return False
    return True


# ==================== 1. 個股基本資訊抓取 ====================
def fetch_listed():
    try:
        r = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", timeout=10)
        data = r.json()
    except Exception as e:
        print(f"Error fetching listed: {e}"); return []
    records = []
    for row in data:
        stock_id = row.get("公司代號", "").strip()
        if not is_valid_stock(stock_id): continue
        shares_str = row.get("已發行普通股數或TDR原股發行股數", "").replace(",", "").strip()
        try: shares = int(shares_str) if shares_str else None
        except ValueError: shares = None
        records.append({
            "stock_id":     stock_id,
            "stock_name":   row.get("公司簡稱", "").strip(),
            "industry":     INDUSTRY_MAP.get(row.get("產業別", "").strip(), "其他"),
            "shares_issued": shares,
            "market":       MARKET_MAP["listed"],
            "updated_at":   date.today().isoformat(),
        })
    return records

def fetch_otc():
    url = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
    except Exception as e:
        print(f"Error fetching OTC: {e}"); return []
    records = []
    for row in data:
        stock_id = row.get("SecuritiesCompanyCode", "").strip()
        if not is_valid_stock(stock_id): continue
        shares_str = row.get("IssueShares", "").replace(",", "").strip()
        try: shares = int(shares_str) if shares_str else None
        except ValueError: shares = None
        records.append({
            "stock_id":     stock_id,
            "stock_name":   row.get("CompanyAbbreviation", "").strip(),
            "industry":     INDUSTRY_MAP.get(row.get("SecuritiesIndustryCode", "").strip(), "其他"),
            "shares_issued": shares,
            "market":       MARKET_MAP["otc"],
            "updated_at":   date.today().isoformat(),
        })
    return records

def save_to_supabase(records):
    if not records: return
    supabase.table("stock_info").upsert(records, on_conflict="stock_id").execute()
    print(f"Done! {len(records)} records saved to stock_info.")

def refresh_stock_info():
    """
    [G] 更新個股基本資訊。任一來源失敗時，沿用資料庫現有清單。
    兩邊都取得時，官方名單裡沒有的股票標上下市日（今天），重新出現的會清除標示。
    """
    listed = fetch_listed()
    otc = fetch_otc()
    if not listed or not otc:
        print("⚠️ 個股基本資訊未能完整取得，缺少的部分沿用資料庫現有清單")
    for r in listed + otc:
        r["delisted_date"] = None
    save_to_supabase(listed)
    save_to_supabase(otc)
    if listed and otc:
        mark_delisted({r["stock_id"] for r in listed + otc})

def mark_delisted(current_ids):
    """股票清單中不在官方現有名單、且還沒標示的股票，下市日記為今天"""
    try:
        active, start = set(), 0
        while True:
            res = supabase.table("stock_info").select("stock_id").is_("delisted_date", "null") \
                .order("stock_id").range(start, start + 999).execute()
            active |= {r["stock_id"] for r in res.data}
            if len(res.data) < 1000:
                break
            start += 1000
        gone = sorted(active - current_ids)
        if len(gone) > 30:
            # 一天不會下市這麼多檔，多半是官方名單暫時不完整，先不標示
            print(f"⚠️ 有 {len(gone)} 檔不在官方名單，疑似名單不完整，略過下市標示")
        elif gone:
            supabase.table("stock_info").update({"delisted_date": date.today().isoformat()}) \
                .in_("stock_id", gone).execute()
            print(f"標示下市 {len(gone)} 檔：{', '.join(gone)}")
    except Exception as e:
        print(f"標示下市股票失敗: {e}")

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


# ==================== 2. 每日股價抓取 ====================
def fetch_twse_daily_all(today_str, valid_stocks):
    url_date = today_str.replace("-", "")
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={url_date}&type=ALLBUT0999&response=json"
    print(f"正在從證交所下載 {today_str} 上市行情...")
    try:
        data = get_json(url, timeout=15)
        if data.get("stat") != "OK":
            print(f"證交所提示: {data.get('stat')}"); return []

        price_table = next(
            (t for t in data.get("tables", []) if "每日收盤行情" in t.get("title", "")),
            None
        )
        if not price_table:
            print("找不到每日收盤行情表"); return []

        records = []
        for row in price_table.get("data", []):
            stock_id = row[0].strip()
            if stock_id not in valid_stocks:
                continue
            records.append({
                "stock_id":    stock_id,
                "date":        today_str,
                "open_price":  parse_price(row[5]),
                "high_price":  parse_price(row[6]),
                "low_price":   parse_price(row[7]),
                "close_price": parse_price(row[8]),
                "volume":      parse_volume(row[2]),
            })
        return records
    except Exception as e:
        print(f"抓取上市行情失敗: {e}"); return []


def fetch_tpex_otc_daily(today_str, valid_stocks):
    url = f"https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={tpex_url_date(today_str)}&type=AL&id=&response=json&order=0&sort=asc"
    print(f"正在從櫃買中心下載 {today_str} 上櫃行情...")
    try:
        data = get_json(url, timeout=30)
        tables = data.get("tables", [])
        if not tables or not tables[0].get("data"):
            print("上櫃股價今日尚未更新或查無資料"); return []
        if not tpex_date_matches(data, today_str, "上櫃行情"):
            return []

        records = []
        for row in tables[0]["data"]:
            stock_id = row[0].strip()
            if stock_id not in valid_stocks:
                continue
            records.append({
                "stock_id":    stock_id,
                "date":        today_str,
                "open_price":  parse_price(row[4]),
                "high_price":  parse_price(row[5]),
                "low_price":   parse_price(row[6]),
                "close_price": parse_price(row[2]),
                "volume":      parse_volume(row[7]),
            })
        return records
    except Exception as e:
        print(f"抓取上櫃行情失敗: {e}"); return []


# ==================== 3. 個股三大法人股數抓取 ====================
def fetch_twse_stock_ins(today_str, valid_stocks):
    url_date = today_str.replace("-", "")
    url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={url_date}&selectType=ALLBUT0999&response=json"
    print(f"正在從證交所下載 {today_str} 上市法人...")
    try:
        data = get_json(url, timeout=15)
        if data.get("stat") != "OK":
            print(f"上市法人尚未更新: {data.get('stat')}"); return []

        records = []
        for row in data.get("data", []):
            if len(row) < 19:
                continue
            stock_id = row[0].strip()
            if stock_id not in valid_stocks:
                continue
            records.append({
                "stock_id":           stock_id,
                "date":               today_str,
                # 外資含外資自營商（row[7]），與上櫃的「外資及陸資合計」定義一致
                "foreign_net_shares": parse_volume(row[4]) + parse_volume(row[7]),
                "sitca_net_shares":   parse_volume(row[10]),
                "dealers_net_shares": parse_volume(row[11]),
            })
        return records
    except Exception as e:
        print(f"抓取上市法人失敗: {e}"); return []


def fetch_tpex_stock_ins(today_str, valid_stocks):
    """
    [B] 上櫃三大法人。欄位（每組為 買進／賣出／買賣超）：
      row[2:5]   外資及陸資（不含外資自營商）   row[5:8]   外資自營商
      row[8:11]  外資及陸資合計                 row[11:14] 投信
      row[14:17] 自營商（自行買賣）             row[17:20] 自營商（避險）
      row[20:23] 自營商合計                     row[23]    三大法人合計
    """
    dt = datetime.strptime(today_str, "%Y-%m-%d")
    if dt.weekday() >= 5:
        print(f"{today_str} 為週末，跳過上櫃法人抓取")
        return []

    url = f"https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date={tpex_url_date(today_str)}&id=&response=json"
    print(f"正在從櫃買中心下載 {today_str} 上櫃法人...")
    try:
        data = get_json(url, timeout=15)
        tables = data.get("tables", [])
        if not tables or not tables[0].get("data"):
            print("上櫃法人今日尚未更新或查無資料"); return []
        if not tpex_date_matches(data, today_str, "上櫃法人"):
            return []

        records = []
        for row in tables[0]["data"]:
            if len(row) < 24:
                continue
            stock_id = row[0].strip()
            if stock_id not in valid_stocks: continue
            records.append({
                "stock_id":           stock_id,
                "date":               today_str,
                "foreign_net_shares": parse_volume(row[10]),
                "sitca_net_shares":   parse_volume(row[13]),
                "dealers_net_shares": parse_volume(row[22]),
            })

        if len(records) < 500:
            print(f"⚠️ 警告：上櫃法人資料筆數異常偏低（{len(records)}筆），請人工確認")

        return records
    except Exception as e:
        print(f"抓取上櫃法人失敗: {e}"); return []


# ==================== 4. 融資融券抓取（餘額＋限額＋使用率，單位一律為「張」）====================
def fetch_twse_margin(today_str, valid_stocks):
    """[C] 上市融資融券。MI_MARGN：row[6]/row[7] 融資餘額/限額，row[12]/row[13] 融券餘額/限額"""
    url_date = today_str.replace("-", "")
    url = f"https://www.twse.com.tw/exchangeReport/MI_MARGN?response=json&date={url_date}&selectType=ALL"
    print(f"正在從證交所下載 {today_str} 上市融資融券...")
    try:
        data = get_json(url, timeout=15)
        if data.get("stat") != "OK":
            print(f"上市融資融券尚未更新: {data.get('stat')}"); return []

        table = next(
            (t for t in data.get("tables", []) if "融資融券彙總" in t.get("title", "")),
            None
        )
        if not table:
            print("找不到融資融券彙總表"); return []

        records = []
        for row in table.get("data", []):
            stock_id = row[0].strip()
            if stock_id not in valid_stocks:
                continue
            margin_balance = parse_volume(row[6])
            margin_limit   = parse_volume(row[7])
            short_balance  = parse_volume(row[12])
            short_limit    = parse_volume(row[13])
            records.append({
                "stock_id":       stock_id,
                "date":           today_str,
                "margin_balance": margin_balance,
                "margin_limit":   margin_limit,
                "margin_rate":    calc_rate(margin_balance, margin_limit),
                "short_balance":  short_balance,
                "short_rate":     calc_rate(short_balance, short_limit),
            })
        return records
    except Exception as e:
        print(f"抓取上市融資融券失敗: {e}"); return []


def fetch_tpex_margin(today_str, valid_stocks):
    """
    [C] 上櫃融資融券。/margin/balance：
      row[6] 資餘額   row[8] 資使用率(%)   row[9] 資限額
      row[14] 券餘額  row[16] 券使用率(%)  row[17] 券限額
    """
    url = f"https://www.tpex.org.tw/www/zh-tw/margin/balance?date={tpex_url_date(today_str)}&id=&response=json"
    print(f"正在從櫃買中心下載 {today_str} 上櫃融資融券...")
    try:
        data = get_json(url, timeout=15)
        tables = data.get("tables", [])
        if not tables or not tables[0].get("data"):
            print("上櫃融資融券今日尚未更新或查無資料"); return []
        if not tpex_date_matches(data, today_str, "上櫃融資融券"):
            return []

        records = []
        for row in tables[0]["data"]:
            if len(row) < 18:
                continue
            stock_id = row[0].strip()
            if stock_id not in valid_stocks:
                continue
            records.append({
                "stock_id":       stock_id,
                "date":           today_str,
                "margin_balance": parse_volume(row[6]),
                "margin_limit":   parse_volume(row[9]),
                "margin_rate":    parse_float(row[8]),
                "short_balance":  parse_volume(row[14]),
                "short_rate":     parse_float(row[16]),
            })
        return records
    except Exception as e:
        print(f"抓取上櫃融資融券失敗: {e}"); return []


# ==================== 5. 大戶持股比例抓取 ====================
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

        result = (
            df.groupby(["資料日期", "證券代號"])["占集保庫存數比例%"]
            .sum()
            .reset_index()
        )

        records = []
        for _, row in result.iterrows():
            records.append({
                "stock_id":         str(row["證券代號"]).strip(),
                "week_date":        parse_date(str(row["資料日期"]).strip()),
                "big_holder_ratio": round(float(row["占集保庫存數比例%"]), 2),
            })

        return records

    except Exception as e:
        print(f"抓取大戶持股失敗: {e}"); return []


# ==================== 6. 月營收抓取 ====================
def parse_year_month(ym_str):
    """民國年月 11503 -> 2026-03"""
    ym_str = str(ym_str).strip()
    if len(ym_str) == 5 and ym_str.isdigit():
        roc_year = int(ym_str[:3])
        month = ym_str[3:5]
        return f"{roc_year + 1911}-{month}"
    return None

def fetch_revenue(url, market_label, valid_stocks):
    print(f"正在下載 {market_label} 月營收...")
    try:
        r = requests.get(url, timeout=15)
        r.encoding = "utf-8"
        data = r.json()
    except Exception as e:
        print(f"抓取 {market_label} 月營收失敗: {e}"); return []

    records = []
    for row in data:
        stock_id = row.get("公司代號", "").strip()
        if stock_id not in valid_stocks:
            continue

        revenue_str = row.get("營業收入-當月營收", "").replace(",", "").strip()
        cumulative_str = row.get("累計營業收入-當月累計營收", "").replace(",", "").strip()

        try: revenue = int(revenue_str) if revenue_str else None
        except ValueError: revenue = None

        try: cumulative = int(cumulative_str) if cumulative_str else None
        except ValueError: cumulative = None

        try: mom = float(row.get("營業收入-上月比較增減(%)", ""))
        except (ValueError, TypeError): mom = None

        try: yoy = float(row.get("營業收入-去年同月增減(%)", ""))
        except (ValueError, TypeError): yoy = None

        records.append({
            "stock_id":           stock_id,
            "year_month":         parse_year_month(row.get("資料年月", "")),
            "revenue":            revenue,
            "mom_growth":         round(mom, 2) if mom is not None else None,
            "yoy_growth":         round(yoy, 2) if yoy is not None else None,
            "cumulative_revenue": cumulative,
            # [K] 來源的其餘欄位（營收金額單位為千元）
            "prev_month_revenue":           parse_float(row.get("營業收入-上月營收", "")),
            "last_year_revenue":            parse_float(row.get("營業收入-去年當月營收", "")),
            "last_year_cumulative_revenue": parse_float(row.get("累計營業收入-去年累計營收", "")),
            "cumulative_yoy_growth":        parse_float(row.get("累計營業收入-前期比較增減(%)", "")),
            "note":                         (str(row.get("備註") or "").strip().strip("-") or None),
            "updated_at":         parse_roc_date(row.get("出表日期", "")),
        })
    return records

def fetch_monthly_revenue(valid_stocks):
    records = []
    records.extend(fetch_revenue(
        "https://openapi.twse.com.tw/v1/opendata/t187ap05_L", "上市", valid_stocks
    ))
    records.extend(fetch_revenue(
        "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O", "上櫃", valid_stocks
    ))
    return records


# ==================== 7. 季報抓取 ====================
# 注意：來源的 EPS 與淨利是「年初至該季」的累計值，單季數字需用本季減上季。
def parse_roc_year(year_str):
    try:
        return int(str(year_str).strip()) + 1911
    except:
        return None

def fetch_with_retry(url, max_retries=3, wait=5):
    """帶重試機制的 GET 請求"""
    for attempt in range(max_retries):
        try:
            r = requests.get(url, timeout=20)
            r.encoding = "utf-8"
            return r.json()
        except Exception as e:
            print(f"  第 {attempt+1} 次失敗 ({url}): {e}")
            if attempt < max_retries - 1:
                print(f"  等待 {wait} 秒後重試...")
                time.sleep(wait)
    return []

# [K] 季報另外存下來的欄位
INCOME_EXTRA = ["operating_cost", "operating_expense", "non_operating_income",
                "pretax_income", "income_tax", "net_income_parent"]
BALANCE_EXTRA = ["current_assets", "non_current_assets", "total_assets",
                 "current_liabilities", "non_current_liabilities", "total_liabilities",
                 "capital_stock", "retained_earnings", "equity_parent"]

NET_INCOME_KEYS = [
    "本期淨利（淨損）",
    "本期稅後淨利（淨損）",
    "繼續營業單位本期純益（純損）",
]

EQUITY_KEYS = [
    "權益總額",
    "權益總計",
]

INCOME_APIS = [
    "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci",
    "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_fh",
    "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ins",
    "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_bd",
    "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_mim",
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ci",
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_fh",
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ins",
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_bd",
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_mim",
]

BALANCE_APIS = [
    "https://openapi.twse.com.tw/v1/opendata/t187ap07_L_ci",
    "https://openapi.twse.com.tw/v1/opendata/t187ap07_L_fh",
    "https://openapi.twse.com.tw/v1/opendata/t187ap07_L_ins",
    "https://openapi.twse.com.tw/v1/opendata/t187ap07_L_bd",
    "https://openapi.twse.com.tw/v1/opendata/t187ap07_L_mim",
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap07_O_ci",
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap07_O_fh",
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap07_O_ins",
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap07_O_bd",
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap07_O_mim",
]

def fetch_income_statements(valid_stocks):
    result = {}
    for url in INCOME_APIS:
        print(f"  下載損益表：{url.split('/')[-1]}")
        data = fetch_with_retry(url)
        if not data:
            continue

        for row in data:
            stock_id = (row.get("公司代號") or row.get("SecuritiesCompanyCode") or "").strip()
            if not stock_id or stock_id not in valid_stocks:
                continue

            net_income = None
            for key in NET_INCOME_KEYS:
                if row.get(key):
                    net_income = parse_float(row[key])
                    break

            updated_at  = parse_roc_date(row.get("出表日期") or row.get("Date") or "")
            year_str    = str(row.get("年度") or row.get("Year") or "").strip()
            quarter_str = str(row.get("季別") or row.get("Season") or "0").strip()

            # [J] 營收、毛利、營業利益（千元，年初累計）。只有一般業的損益表有這些欄位，
            #     金控、保險、證券與少數公司沒有，會是 None
            gross_profit = parse_float(row.get("營業毛利（毛損）淨額", ""))
            if gross_profit is None:
                gross_profit = parse_float(row.get("營業毛利（毛損）", ""))

            result[stock_id] = {
                "year":       parse_roc_year(year_str),
                "quarter":    int(quarter_str) if quarter_str.isdigit() else 0,
                "eps":        parse_float(row.get("基本每股盈餘（元）", "")),
                "net_income": net_income,
                "revenue":          parse_float(row.get("營業收入", "")),
                "gross_profit":     gross_profit,
                "operating_income": parse_float(row.get("營業利益（損失）", "")),
                # [K] 其餘損益項目（千元，年初累計）
                "operating_cost":       first_value(row, ["營業成本"]),
                "operating_expense":    first_value(row, ["營業費用"]),
                "non_operating_income": first_value(row, ["營業外收入及支出"]),
                "pretax_income":        first_value(row, ["稅前淨利（淨損）", "繼續營業單位稅前淨利（淨損）", "繼續營業單位稅前損益"]),
                "income_tax":           first_value(row, ["所得稅費用（利益）", "所得稅（費用）利益"]),
                "net_income_parent":    first_value(row, ["淨利（淨損）歸屬於母公司業主", "淨利（損）歸屬於母公司業主"]),
                "updated_at": updated_at,
            }
    return result

def fetch_balance_sheets(valid_stocks):
    result = {}
    for url in BALANCE_APIS:
        print(f"  下載資產負債表：{url.split('/')[-1]}")
        data = fetch_with_retry(url)
        if not data:
            continue

        for row in data:
            stock_id = (row.get("公司代號") or row.get("SecuritiesCompanyCode") or "").strip()
            if not stock_id or stock_id not in valid_stocks:
                continue

            equity_total = None
            for key in EQUITY_KEYS:
                if row.get(key):
                    equity_total = parse_float(row[key])
                    break

            result[stock_id] = {
                "equity_total": equity_total,
                "book_value":   parse_float(row.get("每股參考淨值", "")),
                # [K] 其餘資產負債項目（千元，期末餘額）。金控與保險沒有流動／非流動的區分
                "current_assets":          first_value(row, ["流動資產"]),
                "non_current_assets":      first_value(row, ["非流動資產"]),
                "total_assets":            first_value(row, ["資產總計", "資產總額"]),
                "current_liabilities":     first_value(row, ["流動負債"]),
                "non_current_liabilities": first_value(row, ["非流動負債"]),
                "total_liabilities":       first_value(row, ["負債總計", "負債總額"]),
                "capital_stock":           first_value(row, ["股本"]),
                "retained_earnings":       first_value(row, ["保留盈餘", "保留盈餘（或累積虧損）"]),
                "equity_parent":           first_value(row, ["歸屬於母公司業主之權益合計", "歸屬於母公司業主權益合計", "歸屬於母公司業主之權益"]),
            }
    return result

def fetch_quarterly_report(valid_stocks):
    print("正在下載損益表...")
    income_data = fetch_income_statements(valid_stocks)

    print("正在下載資產負債表...")
    balance_data = fetch_balance_sheets(valid_stocks)

    print("正在抓取最新收盤價計算 P/B...")
    price_map = {}
    latest_date_res = supabase.table("daily_prices") \
        .select("date").order("date", desc=True).limit(1).execute()

    if latest_date_res.data:
        latest_date = latest_date_res.data[0]["date"]
        cutoff_date = (datetime.strptime(latest_date, "%Y-%m-%d") - timedelta(days=10)).strftime("%Y-%m-%d")

        start = 0
        while True:
            res = supabase.table("daily_prices") \
                .select("stock_id, date, close_price") \
                .gte("date", cutoff_date) \
                .order("date", desc=True) \
                .order("stock_id", desc=False) \
                .range(start, start + 999) \
                .execute()
            if not res.data:
                break
            for row in res.data:
                if row["close_price"] is not None and row["stock_id"] not in price_map:
                    price_map[row["stock_id"]] = row["close_price"]
            if len(res.data) < 1000:
                break
            start += 1000
        print(f"  取得 {len(price_map)} 檔股票最新收盤價")

    records = []
    skipped = 0
    all_stocks = set(income_data.keys()) | set(balance_data.keys())

    for stock_id in all_stocks:
        if stock_id not in valid_stocks:
            continue

        inc  = income_data.get(stock_id, {})
        bal  = balance_data.get(stock_id, {})

        # [H] 沒有年度或季別就無法組成主鍵，不寫入
        if not inc.get("year") or not inc.get("quarter"):
            skipped += 1
            continue

        net_income   = inc.get("net_income")
        equity_total = bal.get("equity_total")
        book_value   = bal.get("book_value")
        close_price  = price_map.get(stock_id)

        pb_ratio = None
        if close_price and book_value and book_value > 0:
            pb_ratio = round(close_price / book_value, 2)

        roe = None
        if net_income and equity_total and equity_total > 0:
            roe = round(net_income / equity_total * 100, 2)

        # [J] 毛利率、營業利益率（%，年初累計）
        revenue          = inc.get("revenue")
        gross_profit     = inc.get("gross_profit")
        operating_income = inc.get("operating_income")
        gross_margin = operating_margin = None
        if revenue and revenue > 0:
            if gross_profit is not None:
                gross_margin = round(gross_profit / revenue * 100, 2)
            if operating_income is not None:
                operating_margin = round(operating_income / revenue * 100, 2)

        # [K] 負債比＝負債÷資產；流動比率＝流動資產÷流動負債（皆為 %）
        total_assets, total_liabilities = bal.get("total_assets"), bal.get("total_liabilities")
        current_assets, current_liabilities = bal.get("current_assets"), bal.get("current_liabilities")
        debt_ratio = current_ratio = None
        if total_assets and total_assets > 0 and total_liabilities is not None:
            debt_ratio = round(total_liabilities / total_assets * 100, 2)
        if current_liabilities and current_liabilities > 0 and current_assets is not None:
            current_ratio = round(current_assets / current_liabilities * 100, 2)

        records.append({
            "stock_id":     stock_id,
            "year":         inc.get("year"),
            "quarter":      inc.get("quarter"),
            "eps":          inc.get("eps"),
            "net_income":   net_income,
            "equity_total": equity_total,
            "book_value":   book_value,
            "pb_ratio":     pb_ratio,
            "roe":          roe,
            "revenue":          revenue,
            "gross_profit":     gross_profit,
            "operating_income": operating_income,
            "gross_margin":     gross_margin,
            "operating_margin": operating_margin,
            **{key: inc.get(key) for key in INCOME_EXTRA},
            **{key: bal.get(key) for key in BALANCE_EXTRA},
            "debt_ratio":       debt_ratio,
            "current_ratio":    current_ratio,
            "updated_at":   inc.get("updated_at"),
        })
    if skipped:
        print(f"  有 {skipped} 檔缺少損益表的年度或季別，已略過")
    return records


# ==================== 9. 期貨三大法人未平倉 ====================
def fetch_futures_institutional():
    url = "https://openapi.taifex.com.tw/v1/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate"
    print("正在下載期貨三大法人未平倉...")

    try:
        r = requests.get(url, timeout=10)
        r.encoding = "utf-8-sig"
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


# ==================== 資料庫儲存工具 ====================
def save_data_batches(table_name, records):
    """[F] 過濾主鍵為空或重複的資料後分批寫入，並如實回報結果。回傳 (成功筆數, 失敗筆數)。"""
    if not records:
        print(f"{table_name}：沒有資料可寫入")
        return 0, 0

    keys = TABLE_KEYS.get(table_name, ())
    if keys:
        by_key = {}
        for r in records:
            if any(r.get(k) is None or r.get(k) == "" for k in keys):
                continue
            by_key[tuple(r[k] for k in keys)] = r  # 主鍵重複時保留最後一筆
        clean = list(by_key.values())
    else:
        clean = list(records)
    dropped = len(records) - len(clean)

    saved = failed = 0
    batch_size = 500
    for i in range(0, len(clean), batch_size):
        batch = clean[i:i + batch_size]
        try:
            supabase.table(table_name).upsert(batch).execute()
            saved += len(batch)
        except Exception as e:
            failed += len(batch)
            print(f"儲存 {table_name} 錯誤 (Index {i}): {e}")

    msg = f"{table_name}：成功寫入 {saved} 筆"
    if failed:
        msg += f"，失敗 {failed} 筆 ❌"
    if dropped:
        msg += f"，略過 {dropped} 筆（主鍵為空或重複）"
    print(msg)
    return saved, failed


# ==================== 交易日工具 ====================
def get_trading_days(start_str, end_str):
    """
    [E] 列出區間內的平日並排除證交所公告的休市日。
    休市清單只涵蓋當年度，也不含颱風假；那些日子來源會回傳空資料，抓取函式會自行跳過。
    """
    print("正在從證交所取得交易日曆...")
    holidays = set()
    try:
        r = requests.get(
            "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule",
            timeout=10
        )
        for item in r.json():
            name = item.get("Name", "")
            if "開始交易" in name or "最後交易" in name:
                continue  # 這些是有開盤的日子
            holiday = parse_roc_date(item.get("Date", ""))
            if holiday:
                holidays.add(holiday)
    except Exception as e:
        print(f"⚠️ 無法取得假日清單，僅排除週末: {e}")

    start = datetime.strptime(start_str, "%Y-%m-%d")
    end   = datetime.strptime(end_str, "%Y-%m-%d")

    trading_days = []
    current = start
    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        if current.weekday() < 5 and date_str not in holidays:
            trading_days.append(date_str)
        current += timedelta(days=1)

    return trading_days


# ==================== 主程式 ====================
if __name__ == "__main__":

    SLEEP_BETWEEN_API    = 2
    SLEEP_BETWEEN_DAYS   = 5
    SLEEP_BATCH_EVERY    = 15
    SLEEP_BATCH_DURATION = 30

    print("==================================================")
    print(" 請選擇執行模式")
    print(" 1. 單一日期")
    print(" 2. 區間日期（自動跳過假日）")
    print("==================================================")

    while True:
        mode = input("請選擇模式（1 或 2）：").strip()
        if mode in ("1", "2"):
            break
        print("❌ 請輸入 1 或 2")

    def input_date(prompt):
        while True:
            val = input(prompt).strip()
            if val == "":
                return datetime.now().strftime("%Y-%m-%d")
            try:
                datetime.strptime(val, "%Y-%m-%d")
                return val
            except ValueError:
                print("❌ 格式錯誤，請使用 YYYY-MM-DD（例如: 2026-06-03）")

    if mode == "1":
        date_input = input_date("請輸入執行日期（例如: 2026-06-03）（直接 Enter = 今天）：")
        date_list = [date_input]
    else:
        print("請輸入起訖日期：")
        start_date = input_date("  開始日期（例如: 2026-06-03）（直接 Enter = 今天）：")
        end_date   = input_date("  結束日期（例如: 2026-06-03）（直接 Enter = 今天）：")
        if start_date > end_date:
            print("❌ 開始日期不能晚於結束日期，程式終止。")
            exit()
        date_list = get_trading_days(start_date, end_date)
        if not date_list:
            print("❌ 指定區間內沒有交易日，程式終止。")
            exit()
        print(f"共找到 {len(date_list)} 個交易日：{date_list[0]} ～ {date_list[-1]}")

    print("==================================================")
    print(" 請選擇要執行的功能（可複選，用逗號分隔）")

    if mode == "1":
        print(" 1. 更新個股基本資訊")
        print(" 2. 同步每日股價")
        print(" 3. 同步三大法人")
        print(" 4. 同步融資融券（餘額＋限額＋使用率）")
        print(" 5. 同步大戶持股比例")
        print(" 6. 同步月營收")
        print(" 7. 同步每日股價 + 三大法人 + 融資融券")
        print(" 8. 同步季報（EPS／淨值／P/B／ROE）")
        print(" 9. 同步期貨三大法人未平倉")
        print("10. （已併入功能 4）")
        print(" 0. 全部執行")
        valid_choices = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10"}
    else:
        print(" 2. 同步每日股價")
        print(" 3. 同步三大法人")
        print(" 4. 同步融資融券（餘額＋限額＋使用率）")
        print(" 7. 同步每日股價 + 三大法人 + 融資融券（全部）")
        print(" 0. 全部執行")
        valid_choices = {"2", "3", "4", "7"}

    print("==================================================")
    if mode == "1":
        print(" ⚠️  注意：功能 1、5、6、8、9 與日期無關，只會執行一次")
        print("==================================================")

    while True:
        choice_input = input("請輸入選項（例如 1,3,5 或 0）：").strip()
        if choice_input == "0":
            choices = valid_choices.copy()
            break
        choices = set(choice_input.replace(" ", "").split(","))
        invalid = choices - valid_choices
        if invalid:
            print(f"❌ 無效選項：{', '.join(sorted(invalid))}，請重新輸入。")
        else:
            break

    if "7" in choices:
        choices.update({"2", "3", "4"})
    if "10" in choices:
        choices.add("4")

    valid_stocks = None
    stock_info_refreshed = False

    def ensure_stock_info():
        """[G] 每次執行只更新一次個股基本資訊"""
        global stock_info_refreshed
        if not stock_info_refreshed:
            print("\n--- 更新個股基本資訊 ---")
            refresh_stock_info()
            stock_info_refreshed = True

    def ensure_valid_stocks():
        global valid_stocks
        if valid_stocks is None:
            ensure_stock_info()
            valid_stocks = get_valid_stock_ids()
            print(f"資料庫內現有 {len(valid_stocks)} 檔股票可供對應。")

    if "1" in choices:
        ensure_stock_info()

    if "5" in choices:
        print("\n--- 步驟 5: 同步大戶持股比例 ---")
        ensure_valid_stocks()
        save_data_batches("holder_distribution", fetch_holder_distribution(valid_stocks))

    if "6" in choices:
        print("\n--- 步驟 6: 同步月營收 ---")
        ensure_valid_stocks()
        save_data_batches("monthly_revenue", fetch_monthly_revenue(valid_stocks))

    if "8" in choices:
        print("\n--- 步驟 8: 同步季報 ---")
        ensure_valid_stocks()
        quarterly_data = fetch_quarterly_report(valid_stocks)
        save_data_batches("quarterly_report", quarterly_data)

    if "9" in choices:
        print("\n--- 步驟 9: 同步期貨三大法人未平倉 ---")
        futures_ins_data = fetch_futures_institutional()
        save_data_batches("futures_institutional", futures_ins_data)

    daily_choices = choices & {"2", "3", "4"}
    if daily_choices:
        ensure_valid_stocks()
        total = len(date_list)
        use_batch_sleep = total > SLEEP_BATCH_EVERY

        for i, today in enumerate(date_list, 1):
            print(f"\n========== [{i}/{total}] 執行日期：{today} ==========")

            if "2" in daily_choices:
                print("\n--- 同步每日股價 ---")
                all_prices = []
                all_prices.extend(fetch_twse_daily_all(today, valid_stocks))
                time.sleep(SLEEP_BETWEEN_API)
                all_prices.extend(fetch_tpex_otc_daily(today, valid_stocks))
                save_data_batches("daily_prices", all_prices)
                time.sleep(SLEEP_BETWEEN_API)

            if "3" in daily_choices:
                print("\n--- 同步三大法人 ---")
                all_ins = []
                all_ins.extend(fetch_twse_stock_ins(today, valid_stocks))
                time.sleep(SLEEP_BETWEEN_API)
                all_ins.extend(fetch_tpex_stock_ins(today, valid_stocks))
                save_data_batches("stock_ins_daily", all_ins)
                time.sleep(SLEEP_BETWEEN_API)

            if "4" in daily_choices:
                print("\n--- 同步融資融券 ---")
                all_margin = []
                all_margin.extend(fetch_twse_margin(today, valid_stocks))
                time.sleep(SLEEP_BETWEEN_API)
                all_margin.extend(fetch_tpex_margin(today, valid_stocks))
                save_data_batches("margin_utilization", all_margin)

            if i < total:
                time.sleep(SLEEP_BETWEEN_DAYS)

            if use_batch_sleep and i % SLEEP_BATCH_EVERY == 0 and i < total:
                print(f"\n⏸️  已完成 {i}/{total} 天，休息 {SLEEP_BATCH_DURATION} 秒後繼續...")
                time.sleep(SLEEP_BATCH_DURATION)

    print("\n✅ 全部執行完畢！")
