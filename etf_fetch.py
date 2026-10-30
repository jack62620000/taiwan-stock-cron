"""
台股 ETF 資料抓取：7 項資料的下載、解析與寫入（ETF 專用的 Supabase 專案）

資料庫連線用 ETF_SUPABASE_URL / ETF_SUPABASE_KEY，和主資料庫（SUPABASE_URL）完全分開，
所以這個檔案不匯入 fetch_stock_info.py（它一載入就會連主資料庫）。
排程與回補的流程在 etf_update.py；建表 SQL 在 sql/etf_tables.sql。

  1. etf_info                ETF 清單：證交所 t187ap47_L（上市）＋櫃買每日行情中 00 開頭的代號（上櫃）
  2. etf_daily_prices        每日價量：證交所 MI_INDEX type=0099P、櫃買 afterTrading/otc
  3. etf_nav                 淨值折溢價：證交所 MIS all_etf.txt（只有當天快照）
  4. etf_dividends           配息：證交所 etfDiv、TWT48U_ALL，櫃買 tpex_exright_prepost
  5. etf_ins_daily           三大法人：證交所 T86 selectType=0099P、櫃買 insti/dailyTrade
  6. etf_monthly_stats       每月統計：投信投顧公會 etf_statement2
  7. etf_holder_distribution 集保持股分級：集保開放資料 1-5

解析函式（parse_*）只吃來源的原始回應、不連網路，tests/test_etf_fetch.py 用樣本測它們。
來源改版、欄位位置變了時，解析函式會回傳空清單而不是存進錯的數字。
"""
import csv
import io
import os
import re
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()

TAIPEI = ZoneInfo("Asia/Taipei")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
           "Referer": "https://www.twse.com.tw/"}
# 證交所 rwd 端點連續請求太快會被防火牆擋（回傳 FOR SECURITY REASONS 錯誤頁），每次請求之間至少等這麼久
MIN_INTERVAL = 4

# 各資料表的主鍵，寫入前過濾不完整或重複的資料
TABLE_KEYS = {
    "etf_info":                ("etf_id",),
    "etf_daily_prices":        ("etf_id", "date"),
    "etf_nav":                 ("etf_id", "date"),
    "etf_dividends":           ("etf_id", "ex_date"),
    "etf_ins_daily":           ("etf_id", "date"),
    "etf_monthly_stats":       ("etf_id", "year_month"),
    "etf_holder_distribution": ("etf_id", "week_date"),
    "etf_margin_daily":        ("etf_id", "date"),
    "etf_sbl_daily":           ("etf_id", "date"),
    "etf_top_holdings":        ("etf_id", "year_month", "rank"),
    "etf_expense_ratio":       ("etf_id", "period"),
    "etf_price_adjustments":   ("etf_id", "date", "event_type"),
}

_client = None


def db():
    """ETF 資料庫連線（第一次用到才建立，測試時不需要真的金鑰）"""
    global _client
    if _client is None:
        from supabase import create_client
        _client = create_client(os.getenv("ETF_SUPABASE_URL"), os.getenv("ETF_SUPABASE_KEY"))
    return _client


def today_taipei():
    return datetime.now(TAIPEI).date()


# ==================== 解析小工具 ====================
def is_etf_code(code):
    """ETF 代號：00 開頭，4～6 碼數字，可能帶一個英文字尾（00679B、00400A、00632R）"""
    return bool(re.fullmatch(r"00\d{2,4}[A-Z]?", str(code).strip()))


def to_float(val):
    """'1,234.5' / 1234.5 → 1234.5；空白、'--'、'-' 等回傳 None"""
    if val is None:
        return None
    s = str(val).replace(",", "").strip()
    if s in ("", "-", "--", "---", "X", "除息", "除權", "null", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_int(val):
    f = to_float(val)
    return int(round(f)) if f is not None else None


def roc_to_iso(val):
    """民國日期 '1151027'、'115/10/27'、'115年10月27日' → '2026-10-27'"""
    s = str(val or "").strip()
    if s.isdigit() and len(s) == 7:
        y, mo, d = int(s[:3]), int(s[3:5]), int(s[5:7])
    else:
        m = re.fullmatch(r"(\d{2,3})\D+(\d{1,2})\D+(\d{1,2})\D*", s)
        if not m:
            return None
        y, mo, d = (int(x) for x in m.groups())
    try:
        return date(y + 1911, mo, d).isoformat()
    except ValueError:
        return None


def ymd_to_iso(val):
    """'20261028' → '2026-10-28'"""
    s = str(val or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return None


def clean_name(name):
    """MIS 的名稱後面會加「(基金之配息來源可能為收益平準金…)」警語，去掉"""
    return re.sub(r"\(基金之配息.*$", "", str(name or "")).strip()


def fields_ok(fields, expected, label):
    """核對欄名：expected 是 {欄位位置: 欄名要包含的文字}；對不上就不寫入"""
    names = [re.sub(r"<[^>]+>", "", str(x)).strip() for x in (fields or [])]
    bad = [f"第 {i} 欄應為「{text}」，實際是「{names[i] if i < len(names) else '（沒有這欄）'}」"
           for i, text in expected.items() if i >= len(names) or text not in names[i]]
    if bad:
        print(f"❌ {label}：欄位和程式預期不同（來源可能改版），不寫入。" + "；".join(bad))
        return False
    return True


def category_of(etf_id, fund_type=""):
    """把證交所的基金類型整理成 6 類；上櫃沒有基金類型，用代號字尾判斷"""
    t = fund_type or ""
    suffix = etf_id[-1] if etf_id[-1].isalpha() else ""
    if suffix == "B" or ("債券" in t and "主動" not in t):
        return "債券"
    if suffix in ("L", "R") or "槓桿" in t or "反向" in t:
        return "槓桿反向"
    if suffix == "U" or "期貨" in t:
        return "期貨商品"
    if suffix == "A" or "主動" in t:
        return "主動式"
    if "國內" in t:
        return "國內股票"
    return "國外股票" if t else None


# ==================== 網路請求 ====================
_last_request = 0.0


def http_get(url, retries=3, wait=10, timeout=60, **kw):
    """GET 並在連線中斷時重試；每次請求之間至少間隔 MIN_INTERVAL 秒"""
    global _last_request
    for attempt in range(1, retries + 1):
        gap = time.time() - _last_request
        if gap < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - gap)
        _last_request = time.time()
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout, **kw)
            r.raise_for_status()
            return r
        except requests.exceptions.RequestException as e:
            if attempt == retries:
                raise
            print(f"  第 {attempt} 次請求失敗（{type(e).__name__}），{wait * attempt} 秒後重試...")
            time.sleep(wait * attempt)


def http_post(url, data, retries=3, wait=10, timeout=90):
    """POST（ASP.NET 表單查詢用），間隔與重試規則同 http_get"""
    global _last_request
    for attempt in range(1, retries + 1):
        gap = time.time() - _last_request
        if gap < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - gap)
        _last_request = time.time()
        try:
            r = requests.post(url, data=data, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.exceptions.RequestException as e:
            if attempt == retries:
                raise
            print(f"  第 {attempt} 次請求失敗（{type(e).__name__}），{wait * attempt} 秒後重試...")
            time.sleep(wait * attempt)


def get_json(url, **kw):
    r = http_get(url, **kw)
    try:
        return r.json()
    except ValueError:
        # 證交所防火牆擋下時回傳 HTML 錯誤頁
        raise RuntimeError(f"回應不是 JSON（可能被來源擋下）：{r.text[:120]!r}")


def tpex_date(day):
    """'2026-10-28' → '2026%2F10%2F28'（櫃買只認這個格式，其他格式會被忽略並回傳最新一天）"""
    return day.replace("-", "%2F")


# ==================== 1. ETF 清單 ====================
def parse_twse_etf_list(rows, today):
    records = []
    for x in rows or []:
        code = str(x.get("基金代號", "")).strip()
        if not is_etf_code(code):
            continue
        fund_type = str(x.get("基金類型", "")).strip()
        index = str(x.get("標的指數/追蹤指數名稱", "")).strip()
        records.append({
            "etf_id": code,
            "etf_name": str(x.get("基金簡稱", "")).strip() or code,
            "market": "上市",
            "category": category_of(code, fund_type),
            "fund_type": fund_type or None,
            "index_name": None if index in ("", "不適用") else index,
            "listing_date": roc_to_iso(x.get("上市日期")),
            "fund_tax_id": str(x.get("基金統一編號", "")).strip() or None,
            "updated_at": today,
        })
    return records


def tpex_etf_list(otc_price_rows, today):
    """上櫃 ETF 清單：櫃買每日行情裡 00 開頭的代號（櫃買 OpenAPI 沒有 ETF 清單）"""
    return [{"etf_id": row[0].strip(), "etf_name": row[1].strip(), "market": "上櫃",
             "category": category_of(row[0].strip()), "updated_at": today}
            for row in otc_price_rows if is_etf_code(row[0])]


def fetch_twse_etf_list(today):
    print("正在下載證交所 ETF 基本資料...")
    return parse_twse_etf_list(get_json("https://openapi.twse.com.tw/v1/opendata/t187ap47_L"), today)


# ==================== 2. 每日價量 ====================
def parse_twse_prices(data, day):
    """MI_INDEX type=0099P：0 代號、2 成交股數、3 筆數、4 金額、5～8 開高低收、9 漲跌符號、10 價差"""
    if not data or data.get("stat") != "OK":
        return []
    table = next((t for t in data.get("tables", []) if "每日收盤行情" in str(t.get("title"))), None)
    if not table or not table.get("data"):
        return []
    if not fields_ok(table.get("fields"), {0: "證券代號", 2: "成交股數", 3: "成交筆數", 4: "成交金額", 5: "開盤價",
                                           6: "最高價", 7: "最低價", 8: "收盤價", 9: "漲跌", 10: "漲跌價差"}, "上市 ETF 行情"):
        return []
    records = []
    for row in table["data"]:
        code = row[0].strip()
        if not is_etf_code(code):
            continue
        diff = to_float(row[10])
        if diff is not None and "-" in re.sub(r"<[^>]+>", "", str(row[9])):
            diff = -diff
        records.append({
            "etf_id": code, "date": day,
            "open_price": to_float(row[5]), "high_price": to_float(row[6]),
            "low_price": to_float(row[7]), "close_price": to_float(row[8]), "change": diff,
            "volume": to_int(row[2]) or 0, "trade_value": to_int(row[4]) or 0, "transactions": to_int(row[3]) or 0,
        })
    return records


def tpex_price_rows(data, day, label="上櫃 ETF 行情"):
    """櫃買 afterTrading/otc 的原始列（已核對日期與欄位）；不符時回傳空清單"""
    tables = (data or {}).get("tables") or []
    if not tables or not tables[0].get("data"):
        return []
    resp = str(data.get("date", "")).strip()
    if resp and resp != day.replace("-", ""):
        print(f"⚠️ {label}：回應日期 {resp} 與要求日期 {day} 不符，跳過")
        return []
    if not fields_ok(tables[0].get("fields"), {0: "代號", 2: "收盤", 3: "漲跌", 4: "開盤", 5: "最高", 6: "最低",
                                               7: "成交股數", 8: "成交金額", 9: "成交筆數"}, label):
        return []
    return tables[0]["data"]


def parse_tpex_prices(rows, day):
    """afterTrading/otc：0 代號、2 收盤、3 漲跌、4～6 開高低、7 成交股數、8 金額、9 筆數"""
    return [{
        "etf_id": row[0].strip(), "date": day,
        "open_price": to_float(row[4]), "high_price": to_float(row[5]),
        "low_price": to_float(row[6]), "close_price": to_float(row[2]), "change": to_float(row[3]),
        "volume": to_int(row[7]) or 0, "trade_value": to_int(row[8]) or 0, "transactions": to_int(row[9]) or 0,
    } for row in rows if is_etf_code(row[0])]


def fetch_twse_prices(day):
    print(f"正在下載 {day} 上市 ETF 行情...")
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={day.replace('-', '')}&type=0099P&response=json"
    return parse_twse_prices(get_json(url), day)


def fetch_tpex_price_rows(day):
    print(f"正在下載 {day} 上櫃行情...")
    url = (f"https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={tpex_date(day)}"
           "&type=AL&id=&response=json&order=0&sort=asc")
    return tpex_price_rows(get_json(url), day)


# ==================== 3. 淨值、折溢價 ====================
def parse_mis_nav(data):
    """
    MIS all_etf.txt：a1[].msgArray[]，a 代號、b 名稱、c 發行單位數、d 單位數增減、e 市價、
    f 預估淨值、g 折溢價%、h 前一營業日淨值、i 日期、j 時間。數值有時是數字、有時是帶逗號的字串。
    回傳 (當天的 etf_nav 列, {代號: 前一營業日淨值})
    """
    rows, prev_nav = [], {}
    for block in (data or {}).get("a1", []) or []:
        for m in block.get("msgArray") or []:
            code = str(m.get("a", "")).strip()
            day = ymd_to_iso(m.get("i"))
            if not is_etf_code(code) or not day:
                continue
            rows.append({
                "etf_id": code, "date": day,
                "market_price": to_float(m.get("e")), "est_nav": to_float(m.get("f")),
                "premium_pct": to_float(m.get("g")),
                "units": to_int(m.get("c")), "units_change": to_int(m.get("d")),
            })
            nav = to_float(m.get("h"))
            if nav:
                prev_nav[code] = nav
    return rows, prev_nav


def fetch_mis_nav():
    print("正在下載 MIS ETF 淨值...")
    r = http_get("https://mis.twse.com.tw/stock/data/all_etf.txt")
    return parse_mis_nav(r.json())


# ==================== 4. 配息 ====================
def parse_twse_etf_div(data):
    """證交所 etfDiv：0 代號、2 除息交易日、3 基準日、4 發放日、5 每單位金額（未公告為 null）"""
    if not data or not fields_ok(data.get("fields"), {0: "證券代號", 2: "除息交易日", 3: "基準日", 4: "發放日",
                                                      5: "收益分配金額"}, "證交所 ETF 配息"):
        return []
    out = []
    for row in data.get("data") or []:
        code, ex = str(row[0]).strip(), roc_to_iso(row[2])
        if is_etf_code(code) and ex:
            out.append({"etf_id": code, "ex_date": ex, "record_date": roc_to_iso(row[3]),
                        "pay_date": roc_to_iso(row[4]), "cash_dividend": to_float(row[5])})
    return out


def parse_twse_exdiv_notice(rows):
    """證交所 TWT48U_ALL 除權息預告：只取 ETF 的除息"""
    out = []
    for x in rows or []:
        code, ex = str(x.get("Code", "")).strip(), roc_to_iso(x.get("Date"))
        if is_etf_code(code) and ex and "息" in str(x.get("Exdividend", "")):
            out.append({"etf_id": code, "ex_date": ex, "cash_dividend": to_float(x.get("CashDividend"))})
    return out


def parse_tpex_exdiv_notice(rows):
    """櫃買 tpex_exright_prepost 除權息預告：只取 ETF 的除息"""
    out = []
    for x in rows or []:
        code = str(x.get("SecuritiesCompanyCode", "")).strip()
        ex = roc_to_iso(x.get("ExRrightsExDividendDate"))
        if is_etf_code(code) and ex and "息" in str(x.get("ExRrightsExDividend", "")):
            cash = to_float(x.get("CashDividend"))
            out.append({"etf_id": code, "ex_date": ex, "cash_dividend": cash or None})
    return out


def merge_dividends(*sources):
    """同一檔同一除息日出現在多個來源時合併，已有的值不會被空值蓋掉"""
    merged = {}
    for rows in sources:
        for r in rows:
            cur = merged.setdefault((r["etf_id"], r["ex_date"]),
                                    {"etf_id": r["etf_id"], "ex_date": r["ex_date"], "record_date": None,
                                     "pay_date": None, "cash_dividend": None})
            for k, v in r.items():
                if v is not None:
                    cur[k] = v
    return list(merged.values())


def fetch_twse_etf_div(start=None, end=None):
    """證交所 etfDiv：不帶日期只回傳今年；帶 startDate/endDate（YYYYMMDD）可查歷史"""
    url = "https://www.twse.com.tw/rwd/zh/ETF/etfDiv?response=json"
    if start:
        url += f"&startDate={start.replace('-', '')}&endDate={(end or today_taipei().isoformat()).replace('-', '')}"
    return parse_twse_etf_div(get_json(url))


def fetch_dividends():
    print("正在下載 ETF 配息...")
    sources = []
    for label, fn in (
        ("證交所 etfDiv", fetch_twse_etf_div),
        ("證交所除息預告", lambda: parse_twse_exdiv_notice(
            get_json("https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL"))),
        ("櫃買除息預告", lambda: parse_tpex_exdiv_notice(
            get_json("https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost"))),
    ):
        try:
            rows = fn()
            print(f"  {label}：{len(rows)} 筆")
            sources.append(rows)
        except Exception as e:
            print(f"⚠️ {label} 失敗：{e}")
    return merge_dividends(*sources)


# ==================== 5. 三大法人 ====================
def parse_twse_ins(data, day):
    """T86 selectType=0099P：外資＝row[4]＋外資自營商 row[7]、投信 row[10]、自營商 row[11]（與 stock_ins_daily 相同）"""
    if not data or data.get("stat") != "OK":
        return []
    if not fields_ok(data.get("fields"), {0: "證券代號", 4: "外陸資買賣超", 7: "外資自營商買賣超",
                                          10: "投信買賣超", 11: "自營商買賣超"}, "上市 ETF 法人"):
        return []
    return [{
        "etf_id": row[0].strip(), "date": day,
        "foreign_net_shares": (to_int(row[4]) or 0) + (to_int(row[7]) or 0),
        "sitca_net_shares": to_int(row[10]) or 0,
        "dealers_net_shares": to_int(row[11]) or 0,
    } for row in data.get("data") or [] if len(row) >= 19 and is_etf_code(row[0])]


def parse_tpex_ins(data, day):
    """insti/dailyTrade：外資及陸資合計 row[10]、投信 row[13]、自營商合計 row[22]（與 stock_ins_daily 相同）"""
    tables = (data or {}).get("tables") or []
    if not tables or not tables[0].get("data"):
        return []
    resp = str(data.get("date", "")).strip()
    if resp and resp != day.replace("-", ""):
        print(f"⚠️ 上櫃 ETF 法人：回應日期 {resp} 與要求日期 {day} 不符，跳過")
        return []
    if not fields_ok(tables[0].get("fields"), {0: "代號", 10: "買賣超", 13: "買賣超", 22: "買賣超", 23: "合計"},
                     "上櫃 ETF 法人"):
        return []
    return [{
        "etf_id": row[0].strip(), "date": day,
        "foreign_net_shares": to_int(row[10]) or 0,
        "sitca_net_shares": to_int(row[13]) or 0,
        "dealers_net_shares": to_int(row[22]) or 0,
    } for row in tables[0]["data"] if len(row) >= 24 and is_etf_code(row[0])]


def fetch_twse_ins(day):
    print(f"正在下載 {day} 上市 ETF 法人...")
    url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={day.replace('-', '')}&selectType=0099P&response=json"
    return parse_twse_ins(get_json(url), day)


def fetch_tpex_ins(day):
    print(f"正在下載 {day} 上櫃 ETF 法人...")
    url = (f"https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW"
           f"&date={tpex_date(day)}&id=&response=json")
    return parse_tpex_ins(get_json(url), day)


# ==================== 5b. 融資融券 ====================
def margin_rate(balance, limit):
    return round(balance / limit * 100, 2) if balance is not None and limit else None


def parse_twse_margin(data, day):
    """MI_MARGN selectType=0099P：6/7 融資今日餘額/限額、12/13 融券今日餘額/限額（張，與個股 margin_utilization 相同）"""
    if not data or data.get("stat") != "OK":
        return []
    table = next((t for t in data.get("tables") or [] if "融資融券彙總" in str(t.get("title"))), None)
    if not table or not fields_ok(table.get("fields"), {0: "代號", 6: "今日餘額", 7: "限額", 12: "今日餘額",
                                                        13: "限額"}, "上市 ETF 融資融券"):
        return []
    out = []
    for row in table.get("data") or []:
        if len(row) < 14 or not is_etf_code(row[0]):
            continue
        mb, ml, sb, sl = to_int(row[6]), to_int(row[7]), to_int(row[12]), to_int(row[13])
        out.append({"etf_id": row[0].strip(), "date": day, "margin_balance": mb, "margin_limit": ml,
                    "margin_rate": margin_rate(mb, ml), "short_balance": sb, "short_rate": margin_rate(sb, sl)})
    return out


def parse_tpex_margin(data, day):
    """margin/balance：6 資餘額、8 資使用率、9 資限額、14 券餘額、16 券使用率（張）"""
    tables = (data or {}).get("tables") or []
    if not tables or not tables[0].get("data"):
        return []
    resp = str(data.get("date", "")).strip()
    if resp and resp != day.replace("-", ""):
        print(f"⚠️ 上櫃 ETF 融資融券：回應日期 {resp} 與要求日期 {day} 不符，跳過")
        return []
    if not fields_ok(tables[0].get("fields"), {0: "代號", 6: "資餘額", 8: "資使用率", 9: "資限額", 14: "券餘額",
                                               16: "券使用率"}, "上櫃 ETF 融資融券"):
        return []
    return [{"etf_id": row[0].strip(), "date": day, "margin_balance": to_int(row[6]), "margin_limit": to_int(row[9]),
             "margin_rate": to_float(row[8]), "short_balance": to_int(row[14]), "short_rate": to_float(row[16])}
            for row in tables[0]["data"] if len(row) >= 18 and is_etf_code(row[0])]


def fetch_margin(day):
    print(f"正在下載 {day} ETF 融資融券...")
    twse = parse_twse_margin(get_json(
        f"https://www.twse.com.tw/exchangeReport/MI_MARGN?response=json&date={day.replace('-', '')}&selectType=0099P"),
        day)
    tpex = parse_tpex_margin(get_json(
        f"https://www.tpex.org.tw/www/zh-tw/margin/balance?date={tpex_date(day)}&id=&response=json"), day)
    return twse + tpex


# ==================== 5c. 借券賣出餘額 ====================
# 信用額度總量管制餘額表：欄 8～13 是借券賣出（前日餘額、當日賣出、當日還券、當日調整、當日餘額、次一營業日可借券賣出限額），單位：股
SBL_TWSE_COLUMNS = {0: "代號", 9: "當日賣出", 10: "當日還券", 11: "當日調整", 12: "當日餘額", 13: "限額"}
SBL_TPEX_COLUMNS = {0: "代號", 9: "當日賣出", 10: "當日還券", 11: "當日調整", 12: "當日餘額", 13: "借券賣出限額"}


def sbl_rows(rows, day):
    return [{"etf_id": r[0].strip(), "date": day, "sbl_sell": to_int(r[9]), "sbl_return": to_int(r[10]),
             "sbl_adjust": to_int(r[11]), "sbl_balance": to_int(r[12]), "sbl_limit": to_int(r[13])}
            for r in rows if len(r) >= 14 and is_etf_code(r[0])]


def parse_twse_sbl(data, day):
    """TWT93U（全部上市證券，只留 ETF）"""
    if not data or data.get("stat") != "OK":
        return []
    if str(data.get("date", "")).strip() not in ("", day.replace("-", "")):
        print(f"⚠️ 上市借券：回應日期 {data.get('date')} 與要求日期 {day} 不符，跳過")
        return []
    if not fields_ok(data.get("fields"), SBL_TWSE_COLUMNS, "上市 ETF 借券賣出"):
        return []
    return sbl_rows(data.get("data") or [], day)


def parse_tpex_sbl(data, day):
    """margin/sbl（全部上櫃證券，只留 ETF）"""
    tables = (data or {}).get("tables") or []
    if not tables or not tables[0].get("data"):
        return []
    resp = str(data.get("date", "")).strip()
    if resp and resp != day.replace("-", ""):
        print(f"⚠️ 上櫃借券：回應日期 {resp} 與要求日期 {day} 不符，跳過")
        return []
    if not fields_ok(tables[0].get("fields"), SBL_TPEX_COLUMNS, "上櫃 ETF 借券賣出"):
        return []
    return sbl_rows(tables[0]["data"], day)


def fetch_sbl(day):
    print(f"正在下載 {day} ETF 借券賣出餘額...")
    twse = parse_twse_sbl(get_json(
        f"https://www.twse.com.tw/exchangeReport/TWT93U?response=json&date={day.replace('-', '')}"), day)
    tpex = parse_tpex_sbl(get_json(
        f"https://www.tpex.org.tw/www/zh-tw/margin/sbl?date={tpex_date(day)}&id=&response=json"), day)
    return twse + tpex


# ==================== 6. 每月統計（投信投顧公會） ====================
SITCA_COLUMNS = {2: "證券代號", 4: "基金統編", 7: "計價幣別", 8: "基金規模(台幣)", 11: "單位淨值(台幣)",
                 13: "受益權單位數", 14: "自然人受益人數", 15: "法人受益人數", 16: "總受益人數",
                 17: "本月申購總金額(台幣)", 19: "本月買回總金額(台幣)", 21: "定期定額扣款筆數",
                 22: "定期定額扣款戶數", 23: "定期定額扣款金額(台幣)"}


def html_table_rows(html):
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [re.sub(r"<[^>]+>|&nbsp;", "", c).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        if len(cells) >= 20:
            out.append(cells)
    return out


def parse_sitca_monthly(html, year_month, code_by_tax_id=None):
    """
    回傳 (etf_monthly_stats 列, {代號: 基金統編})。
    約 2024 年以前的月份沒有證券代號（顯示 -），用 code_by_tax_id（基金統編 → 代號）對回代號。
    """
    rows = html_table_rows(html)
    if not rows:
        return [], {}
    if not fields_ok(rows[0], SITCA_COLUMNS, f"投信公會 {year_month}"):
        return [], {}
    code_by_tax_id = code_by_tax_id or {}
    records, tax_ids = {}, {}
    for c in rows[1:]:
        tax_id = c[4].strip()
        code = c[2].strip()
        if not is_etf_code(code):
            code = code_by_tax_id.get(tax_id, "")
        if not is_etf_code(code) or code in records:
            continue  # 同一檔有多個級別時保留第一列
        tax_ids[code] = tax_id
        records[code] = {
            "etf_id": code, "year_month": year_month,
            "aum": to_int(c[8]), "nav": to_float(c[11]), "units": to_int(c[13]),
            "holders_natural": to_int(c[14]), "holders_legal": to_int(c[15]), "holders_total": to_int(c[16]),
            "subscription_amount": to_int(c[17]), "redemption_amount": to_int(c[19]),
            "dca_count": to_int(c[21]), "dca_accounts": to_int(c[22]), "dca_amount": to_int(c[23]),
        }
    return list(records.values()), tax_ids


def fetch_sitca_statement(year_month):
    print(f"正在下載投信公會 {year_month} ETF 月資料...")
    ym = year_month.replace("-", "")
    r = http_get(f"https://www.sitca.org.tw/ROC/SITCA_ETF/etf_statement2.aspx?txtYM={ym}&txtemcode=", timeout=90)
    return r.content.decode("utf-8", "ignore")


def fetch_sitca_monthly(year_month, code_by_tax_id=None):
    return parse_sitca_monthly(fetch_sitca_statement(year_month), year_month, code_by_tax_id)


# ==================== 6b. 前十大持股（投信投顧公會「基金投資明細－月前十大」） ====================
SITCA_TOP10_URL = "https://www.sitca.org.tw/ROC/Industry/IN2629.aspx?pid=IN22601_04"
# 公會的 ETF 類型：指數股票型（AH）與主動式交易所交易基金（AL）
SITCA_ETF_CLASSES = ["AH11", "AH12", "AH13", "AH14", "AH15", "AH21", "AH22", "AH23", "AH24", "AH25", "AH26",
                     "AL11", "AL12", "AL19", "AL21", "AL22", "AL29"]
TOP10_COLUMNS = {0: "基金名稱", 1: "名次", 2: "標的種類", 3: "標的代號", 4: "標的名稱", 5: "金額", 9: "比例"}


def fund_name_key(name):
    """基金名稱比對用：去掉結尾的警語「(本基金之配息來源…)」「(本基金採匯率避險)」、級別「-新台幣(配息)」、空白，全形括號改半形"""
    name = re.sub(r"<[^>]+>|&nbsp;|\s+", "", name or "").replace("（", "(").replace("）", ")")
    name = re.sub(r"\((本子?基金|基金|[^()]*(配息|保證|風險))[^()]*\)$", "", name)
    return re.sub(r"-(新台幣|新臺幣|美元|人民幣|日圓)(\(.*\))?$", "", name)


def parse_sitca_names(html, code_by_tax_id=None):
    """投信公會月資料 → {基金名稱: 代號}；沒有代號的舊月份用基金統編對回"""
    code_by_tax_id = code_by_tax_id or {}
    rows = html_table_rows(html)
    if not rows or not fields_ok(rows[0], {2: "證券代號", 4: "基金統編", 5: "基金名稱"}, "投信公會基金名稱"):
        return {}
    out = {}
    for c in rows[1:]:
        code = c[2].strip() if is_etf_code(c[2]) else code_by_tax_id.get(c[4].strip(), "")
        if is_etf_code(code):
            out.setdefault(fund_name_key(c[5]), code)
    return out


def parse_sitca_top10(html, year_month, code_by_name):
    """回傳 (etf_top_holdings 列, 對不到代號的基金名稱)"""
    out, unknown, fund = [], set(), None
    started = False
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [re.sub(r"<[^>]+>|&nbsp;", " ", c).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        cells = [re.sub(r"\s+", " ", c) for c in cells]
        if not started:
            if cells and cells[0] == "基金名稱":
                if not fields_ok(cells, TOP10_COLUMNS, f"投信公會前十大 {year_month}"):
                    return [], set()
                started = True
            continue
        if len(cells) == 10:
            fund, data = cells[0], cells[1:]
        elif len(cells) == 9:
            data = cells
        else:
            continue  # 合計列
        if not fund or not data[0].isdigit():
            continue
        code = code_by_name.get(fund_name_key(fund))
        if not code:
            unknown.add(fund_name_key(fund))
            continue
        out.append({"etf_id": code, "year_month": year_month, "rank": int(data[0]), "holding_type": data[1],
                    "holding_code": data[2] or None, "holding_name": data[3], "amount": to_int(data[4]),
                    "weight_pct": to_float(data[8])})
    return out, unknown


def sitca_hidden_fields(html):
    return dict(re.findall(r'<input[^>]+name="(__[A-Z]+)"[^>]+value="([^"]*)"', html))


def sitca_selected_month(html):
    sel = re.search(r'<select[^>]*ddlQ_YM[^>]*>(.*?)</select>', html, re.S)
    got = re.search(r'<option[^>]*selected="selected"[^>]*value="(\d{6})"', sel.group(1)) if sel else None
    return got.group(1) if got else ""


def fetch_sitca_top10(year_month, code_by_name):
    """
    逐一查詢 ETF 類型（ASP.NET 表單，只查這個月份選單裡有的類型）。
    網頁預設是最新月份；查其他月份要先送出「切換月份」的 postback 拿新的 __VIEWSTATE，
    否則伺服器會忽略月份與類型，回傳預設公司的資料。
    回傳的頁面月份不對、或大部分基金對不到 ETF 代號時，整個月份當作失敗（回傳空清單）。
    """
    print(f"正在下載投信公會 {year_month} ETF 前十大持股...")
    ym = year_month.replace("-", "")
    query = {"ctl00$ContentPlaceHolder1$ddlQ_YM": ym, "ctl00$ContentPlaceHolder1$rdo1": "rbClass"}
    page = http_get(SITCA_TOP10_URL).content.decode("utf-8", "ignore")
    form = sitca_hidden_fields(page)
    form.update(query, __EVENTTARGET="ctl00$ContentPlaceHolder1$ddlQ_YM", __EVENTARGUMENT="")
    page = http_post(SITCA_TOP10_URL, data=form).content.decode("utf-8", "ignore")
    hidden = sitca_hidden_fields(page)
    # 舊月份沒有的類型（例如 2025 年以前沒有主動式 ETF）不在選單裡，送出會被伺服器拒絕
    sel = re.search(r'<select[^>]*ddlQ_Class"[^>]*>(.*?)</select>', page, re.S)
    offered = set(re.findall(r'value="([^"]*)"', sel.group(1))) if sel else set(SITCA_ETF_CLASSES)
    rows, unknown, funds = [], set(), set()
    for cls in [c for c in SITCA_ETF_CLASSES if c in offered]:
        form = dict(hidden)
        form.update(query)
        form.update({"ctl00$ContentPlaceHolder1$ddlQ_Class": cls, "ctl00$ContentPlaceHolder1$BtnQuery": "查詢"})
        html = http_post(SITCA_TOP10_URL, data=form).content.decode("utf-8", "ignore")
        if sitca_selected_month(html) != ym:
            print(f"  ⚠️ 投信公會回傳的月份是 {sitca_selected_month(html) or '?'}，不是 {ym}，這個月份不寫入")
            return []
        got, miss = parse_sitca_top10(html, year_month, code_by_name)
        rows += got
        unknown |= miss
        funds |= {r["etf_id"] for r in got}
    if unknown:
        print(f"  {len(unknown)} 檔基金名稱對不到代號（例：{sorted(unknown)[:3]}）")
    if len(unknown) > len(funds):
        print(f"  ⚠️ 對不到代號的基金太多（{len(unknown)} 檔，對到 {len(funds)} 檔），這個月份不寫入")
        return []
    return rows


# ==================== 6c. 各項費用比率（投信投顧公會「各項費用比率(月、季、年)」） ====================
SITCA_FEE_URL = "https://www.sitca.org.tw/ROC/Industry/IN2211.aspx?pid=IN2222_01"
FEE_FORM = "ctl00$ContentPlaceHolder1$"
# 月份選單的值 → 期間：'9' → 2026-09、'0630' → 2026-Q2、'Year' → 2026
FEE_QUARTERS = {"0331": "Q1", "0630": "Q2", "0930": "Q3", "1231": "Q4"}
# 欄位順序；多數期間有 ETF 申購買回交易費（a3），2021 年等早期資料沒有這欄（表頭有時仍列出 a3，以每列欄數為準）
FEE_FIELDS_A3 = ["brokerage", "tax", "creation", "mgmt", "custody", "guarantee", "other", "total"]
FEE_FIELDS_OLD = ["brokerage", "tax", "mgmt", "custody", "guarantee", "other", "total"]
FEE_LABELS = {"brokerage": "a1", "tax": "a2", "creation": "a3", "mgmt": "b1", "custody": "b2",
              "guarantee": "b3", "other": "b4"}


def fee_period(year, month_value):
    """('2026', '9') → ('2026-09', 'month')；('2026', '0630') → ('2026-Q2', 'quarter')；('2025', 'Year') → ('2025', 'year')"""
    if month_value == "Year":
        return str(year), "year"
    if month_value in FEE_QUARTERS:
        return f"{year}-{FEE_QUARTERS[month_value]}", "quarter"
    return f"{year}-{int(month_value):02d}", "month"


def fee_cells(tr):
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>|&nbsp;", " ", c)).strip()
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]


def parse_sitca_fees(html, period, period_type, code_by_tax_id):
    """回傳 etf_expense_ratio 列（比率單位：%）；用基金統編對回 ETF 代號，不是 ETF 的基金略過"""
    header, out = False, {}
    layouts = {3 + 2 * len(fl): fl for fl in (FEE_FIELDS_A3, FEE_FIELDS_OLD)}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = fee_cells(tr)
        if not header:
            if cells and "手續費" in cells[0] and "( a1 )" in cells[0]:
                fields = FEE_FIELDS_A3 if any("( a3 )" in c for c in cells) else FEE_FIELDS_OLD
                expected = {i: f"( {FEE_LABELS[k]} )" for i, k in enumerate(fields[:-1])}
                if len(cells) != len(fields) - 1 or not fields_ok(cells, expected, f"投信公會費用率 {period}"):
                    return []
                header = True
            continue
        fields = layouts.get(len(cells))
        if not fields or not re.fullmatch(r"\d{8}", cells[1]):
            continue
        code = code_by_tax_id.get(cells[1])
        if not code or code in out:
            continue
        rec = {"etf_id": code, "period": period, "period_type": period_type, "creation_pct": None}
        for i, k in enumerate(fields):
            rec[f"{k}_pct"] = to_float(cells[4 + 2 * i].rstrip("%"))
        rec["total_amount"] = to_int(cells[3 + 2 * (len(fields) - 1)])
        out[code] = rec
    return list(out.values())


def sitca_selected(html, name):
    sel = re.search(rf'<select[^>]*name="{re.escape(FEE_FORM + name)}"[^>]*>(.*?)</select>', html, re.S)
    got = re.search(r'<option[^>]*selected="selected"[^>]*value="([^"]*)"', sel.group(1)) if sel else None
    return got.group(1) if got else ""


def sitca_options(html, name):
    sel = re.search(rf'<select[^>]*name="{re.escape(FEE_FORM + name)}"[^>]*>(.*?)</select>', html, re.S)
    return re.findall(r'value="([^"]*)"', sel.group(1)) if sel else []


def open_sitca_fee_year(year):
    """切換到某一年（ASP.NET postback），回傳 (隱藏欄位, 這一年可查的月份選單值)"""
    page = http_get(SITCA_FEE_URL).content.decode("utf-8", "ignore")
    form = sitca_hidden_fields(page)
    form.update({FEE_FORM + "ddlQ_Y": str(year), FEE_FORM + "ddlQ_M": "Year",
                 "__EVENTTARGET": FEE_FORM + "ddlQ_Y", "__EVENTARGUMENT": ""})
    page = http_post(SITCA_FEE_URL, data=form).content.decode("utf-8", "ignore")
    if sitca_selected(page, "ddlQ_Y") != str(year):
        return {}, []
    return sitca_hidden_fields(page), sitca_options(page, "ddlQ_M")


def fetch_sitca_fees(year, month_value, code_by_tax_id, hidden):
    """查一個期間（所有公司、所有基金）；回傳的年份或月份不對就當作失敗"""
    period, period_type = fee_period(year, month_value)
    print(f"正在下載投信公會 {period} 各項費用比率...")
    form = dict(hidden)
    form.update({FEE_FORM + "ddlQ_Y": str(year), FEE_FORM + "ddlQ_M": month_value, FEE_FORM + "ddlQ_Comid": "",
                 FEE_FORM + "ddlQ_Fund": "", FEE_FORM + "BtnQuery": "查詢"})
    html = http_post(SITCA_FEE_URL, data=form, timeout=180).content.decode("utf-8", "ignore")
    got = (sitca_selected(html, "ddlQ_Y"), sitca_selected(html, "ddlQ_M"))
    if got != (str(year), month_value):
        print(f"  ⚠️ 投信公會回傳的是 {got}，不是 {(str(year), month_value)}，這個期間不寫入")
        return []
    return parse_sitca_fees(html, period, period_type, code_by_tax_id)


# ==================== 7. 集保持股分級 ====================
BIG_LEVELS = range(12, 16)    # 400 張以上
SMALL_LEVELS = range(1, 6)    # 20 張以下
ALL_LEVELS = range(1, 16)     # 16 是差異數調整、17 是合計，不算


def summarize_levels(levels):
    """levels: {分級: (人數, 比例%)} → 每週摘要三個欄位"""
    return {
        "total_holders": sum(levels[l][0] for l in ALL_LEVELS if l in levels),
        "big_holder_ratio": round(sum(levels[l][1] for l in BIG_LEVELS if l in levels), 2),
        "small_holder_ratio": round(sum(levels[l][1] for l in SMALL_LEVELS if l in levels), 2),
    }


def parse_tdcc_csv(text):
    """集保 1-5：資料日期, 證券代號, 持股分級, 人數, 股數, 占集保庫存數比例%"""
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    header = next(reader, [])
    if not fields_ok(header, {0: "資料日期", 1: "證券代號", 2: "持股分級", 3: "人數", 5: "比例"}, "集保持股分級"):
        return []
    by_etf = {}
    for row in reader:
        if len(row) < 6 or not is_etf_code(row[1]):
            continue
        key = (row[1].strip(), ymd_to_iso(row[0]))
        level = to_int(row[2])
        if key[1] and level:
            by_etf.setdefault(key, {})[level] = (to_int(row[3]) or 0, to_float(row[5]) or 0.0)
    return [{"etf_id": code, "week_date": week, **summarize_levels(levels)}
            for (code, week), levels in by_etf.items() if levels]


def fetch_tdcc_holders():
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    print("正在下載集保持股分級...")
    r = http_get("https://smart.tdcc.com.tw/opendata/getOD.ashx?id=1-5", verify=False, timeout=120)
    return parse_tdcc_csv(r.content.decode("utf-8-sig", "ignore"))


# 集保查詢網頁（回補過去約 51 週）：一次只能查一檔一週
TDCC_QUERY_URL = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"
_TDCC_ROW = re.compile(
    r"<tr>\s*<td[^>]*>(\d+)</td>\s*<td[^>]*>[^<]*</td>\s*<td[^>]*>([\d,]+)</td>"
    r"\s*<td[^>]*>[\d,]+</td>\s*<td[^>]*>([\d.]+)</td>"
)
_TDCC_TOKEN = re.compile(r'name="SYNCHRONIZER_TOKEN"[^>]*value="([^"]+)"')


def parse_tdcc_query(html):
    levels = {int(l): (to_int(n) or 0, float(p)) for l, n, p in _TDCC_ROW.findall(html)}
    return summarize_levels(levels) if levels else None


class TdccQuery:
    """查詢網頁要先取得表單代碼，每次查詢的回應會帶下一次要用的代碼（與 fetch_holder_history.py 相同做法）"""

    def __init__(self):
        self.session, self.token, self.weeks = None, "", []

    def open(self):
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": HEADERS["User-Agent"]})
        r = self.session.get(TDCC_QUERY_URL, timeout=30, verify=False)
        r.raise_for_status()
        m = _TDCC_TOKEN.search(r.text)
        self.token = m.group(1) if m else ""
        self.weeks = sorted(ymd_to_iso(d) for d in re.findall(r'<option value="(\d{8})"', r.text))

    def week_summary(self, etf_id, week):
        d = week.replace("-", "")
        data = {"SYNCHRONIZER_TOKEN": self.token, "SYNCHRONIZER_URI": "/portal/zh/smWeb/qryStock",
                "method": "submit", "firDate": d, "scaDate": d, "sqlMethod": "StockNo",
                "stockNo": etf_id, "stockName": ""}
        r = self.session.post(TDCC_QUERY_URL, data=data, timeout=20, verify=False)
        r.raise_for_status()
        m = _TDCC_TOKEN.search(r.text)
        if m:
            self.token = m.group(1)
        return parse_tdcc_query(r.text)


# ==================== 寫入與查詢 ====================
def save(table, records, dry_run=False):
    """過濾主鍵為空或重複的資料後分批 upsert，回傳 (成功筆數, 失敗筆數)"""
    keys = TABLE_KEYS[table]
    by_key = {}
    for r in records:
        if all(r.get(k) not in (None, "") for k in keys):
            by_key[tuple(r[k] for k in keys)] = r
    clean = list(by_key.values())
    if dry_run:
        print(f"{table}：（試跑，不寫入）{len(clean)} 筆")
        return len(clean), 0
    saved = failed = 0
    for i in range(0, len(clean), 500):
        batch = clean[i:i + 500]
        for attempt in range(3):
            try:
                db().table(table).upsert(batch).execute()
                saved += len(batch)
                break
            except Exception as e:
                if attempt == 2:
                    failed += len(batch)
                    print(f"❌ 寫入 {table} 失敗（第 {i} 筆起）：{e}")
                else:
                    time.sleep(5 * (attempt + 1))
    print(f"{table}：成功寫入 {saved} 筆" + (f"，失敗 {failed} 筆 ❌" if failed else ""))
    return saved, failed


def select_all(table, columns, **eq):
    """分頁讀完整張表（supabase 單次上限 1000 筆）"""
    rows, start = [], 0
    while True:
        q = db().table(table).select(columns)
        for col, val in eq.items():
            q = q.eq(col, val)
        res = q.order(columns.split(",")[0].strip()).range(start, start + 999).execute()
        rows += res.data
        if len(res.data) < 1000:
            return rows
        start += 1000


def ensure_bucket(name):
    """Storage 沒有這個桶就建立（不公開），封存舊資料用"""
    storage = db().storage
    names = {getattr(b, "name", None) or b.get("name") for b in storage.list_buckets()}
    if name not in names:
        storage.create_bucket(name, options={"public": False})
        print(f"已建立 Storage 桶 {name}（不公開）")


def archive_day(table, col, day, bucket="archive"):
    """
    把 table 在 day 的全部資料存成 csv.gz 上傳到 ETF 資料庫的 Storage，再下載回來確認筆數一致。回傳筆數。
    檔案位置和主資料庫相同：桶 / 資料表 / 年 / 日期.csv.gz。任何一步失敗都會丟出錯誤，呼叫端就不會刪除資料。
    """
    import gzip
    import io
    import pandas as pd
    from archive import archive_path
    rows, start = [], 0
    while True:
        res = db().table(table).select("*").eq(col, day).order("etf_id").range(start, start + 999).execute()
        rows += res.data or []
        if len(res.data or []) < 1000:
            break
        start += 1000
    if not rows:
        return 0
    data = gzip.compress(pd.DataFrame(rows).to_csv(index=False).encode("utf-8"))
    path = archive_path(table, day)
    store = db().storage.from_(bucket)
    store.upload(path, data, {"content-type": "application/gzip", "upsert": "true"})
    saved = pd.read_csv(io.BytesIO(store.download(path)), compression="gzip", dtype=str)
    if len(saved) != len(rows):
        raise RuntimeError(f"{path} 封存後只讀回 {len(saved)} 筆，應有 {len(rows)} 筆")
    return len(rows)


def select_range(table, columns, col, start, end):
    """分頁讀 col 介於 start～end 的資料（supabase 單次上限 1000 筆）"""
    rows, offset = [], 0
    while True:
        res = (db().table(table).select(columns).gte(col, start).lte(col, end)
               .order(TABLE_KEYS[table][0]).order(col).range(offset, offset + 999).execute())
        rows += res.data
        if len(res.data) < 1000:
            return rows
        offset += 1000


# ==================== 還原權值事件（除息、分割、反分割） ====================
# 不用另外的資料來源：證交所、櫃買的「漲跌」是相對調整後參考價，所以 (收盤 − 漲跌) ÷ 前一天收盤 = 參考價 ÷ 前收。
# 但沒有成交的隔天漲跌常記成 0，用這個比例只認大幅變動（分割／反分割）；除息用配息表的金額計算。
SPLIT_MIN_CHANGE = 0.3   # 參考價和前收差超過 30% 才當成分割／反分割
SPLIT_RATIO_TOL = 0.02   # 比例（或倒數）要接近整數，例如一拆四 0.25、五合一 5


def _whole_ratio(x):
    """x 或 1/x 接近整數時回傳 True（分割比例都是整數）"""
    r = x if x >= 1 else 1 / x
    return abs(r - round(r)) <= SPLIT_RATIO_TOL * round(r)


def build_adjustments(prices, dividends):
    """
    prices：etf_daily_prices 列（etf_id、date、close_price、change），dividends：etf_dividends 列（etf_id、ex_date、cash_dividend）
    回傳 etf_price_adjustments 列；factor = 參考價 ÷ 前收，事件日之前的股價乘上 factor 就能和之後連續
    """
    cash = {(d["etf_id"], d["ex_date"]): to_float(d.get("cash_dividend")) for d in dividends}
    out, last = [], {}
    for r in sorted(prices, key=lambda r: (r["etf_id"], r["date"])):
        key, close = (r["etf_id"], r["date"]), to_float(r.get("close_price"))
        prev = last.get(r["etf_id"])
        div = cash.get(key)
        if prev and div and 0 < div < prev:
            ref = round(prev - div, 4)
            out.append({"etf_id": key[0], "date": key[1], "event_type": "除息", "prev_close": prev,
                        "ref_price": ref, "factor": round(ref / prev, 8), "source": "配息表"})
        change = to_float(r.get("change"))
        if prev and close and change is not None:
            ref = round(close - change, 4)
            ratio = ref / prev
            if ref > 0 and abs(ratio - 1) > SPLIT_MIN_CHANGE and _whole_ratio(ratio):
                # 分割比例是整數，取整後再算，避免參考價四捨五入的誤差（一拆四 = 0.25）
                factor = 1 / round(1 / ratio) if ratio < 1 else float(round(ratio))
                out.append({"etf_id": key[0], "date": key[1], "event_type": "分割" if ratio < 1 else "反分割",
                            "prev_close": prev, "ref_price": round(prev * factor, 4), "factor": round(factor, 8),
                            "source": "漲跌推算"})
        if close:
            last[r["etf_id"]] = close
    return out


def count_rows(table, **eq):
    q = db().table(table).select(TABLE_KEYS[table][0], count="exact")
    for col, val in eq.items():
        q = q.eq(col, val)
    return q.limit(1).execute().count or 0


# ==================== 交易日 ====================
def trading_days(start, end):
    """區間內的平日，排除證交所公告的休市日（只涵蓋當年度；其他休市日來源會回傳空資料，自動跳過）"""
    holidays = set()
    try:
        for item in get_json("https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"):
            name = item.get("Name", "")
            if "開始交易" in name or "最後交易" in name:
                continue
            d = roc_to_iso(item.get("Date"))
            if d:
                holidays.add(d)
    except Exception as e:
        print(f"⚠️ 無法取得休市日，只排除週末：{e}")
    out, d = [], date.fromisoformat(start)
    while d <= date.fromisoformat(end):
        if d.weekday() < 5 and d.isoformat() not in holidays:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out
