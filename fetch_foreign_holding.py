"""
外資持股 - 每檔股票每天的外資及陸資持有股數與持股比率

資料來源（2026-10 實測，都可以查過去日期，一次一天全市場）：
  上市：證交所 MI_QFIIS（外資及陸資投資持股統計）
  上櫃：櫃買中心 insti/qfii（僑外資及陸資持股比例排行表）

寫入 stock_ins_daily 的 foreign_hold_shares（股）、foreign_hold_ratio（%）兩欄，
只更新這兩欄，不動同一列的三大法人買賣超。
auto_fetch.py 每天把當天的持股併進三大法人一起寫入；
backfill.py 用 DATASETS=foreign 回補指定區間（只補資料庫已有三大法人的股票和日期）。
直接執行這個檔案並設 DRY_RUN=1、START_DATE、END_DATE，只會印出抓到的筆數和範例，不寫入資料庫。
"""
import os
import sys
import time

import requests

import fetch_stock_info as f

TABLE = "stock_ins_daily"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
SLEEP_BETWEEN_API = 3
RETRIES = 3


def _get_json(url):
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == RETRIES - 1:
                raise
            time.sleep(10 * (attempt + 1))


def _ratio(val):
    """2.02 或 '87.84%' → 數字"""
    return f.parse_float(str(val).replace("%", "")) if val not in (None, "") else None


def fetch_twse(day, valid_stocks):
    data = _get_json(f"https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS?date={day.replace('-', '')}"
                     f"&selectType=ALLBUT0999&response=json")
    if data.get("stat") != "OK":
        return {}
    # 證券代號、名稱、國際編碼、發行股數、尚可投資股數、外資及陸資持有股數、尚可投資比率、外資及陸資持股比率…
    return {r[0].strip(): (f.parse_volume(r[5]), _ratio(r[7]))
            for r in data.get("data", []) if r[0].strip() in valid_stocks}


def fetch_tpex(day, valid_stocks):
    data = _get_json(f"https://www.tpex.org.tw/www/zh-tw/insti/qfii?date={day.replace('-', '/')}&response=json")
    tables = data.get("tables") or [{}]
    # 排行、代號、名稱、發行股數、尚可投資股數、僑外資及陸資持有股數、尚可投資比率、僑外資及陸資持股比率…
    return {r[1].strip(): (f.parse_volume(r[5]), _ratio(r[7]))
            for r in tables[0].get("data", []) if r[1].strip() in valid_stocks}


def fetch_day(day, valid_stocks):
    """抓某一天上市＋上櫃的外資持股。回傳 ({股票代號: (持有股數, 持股比率)}, 問題清單)"""
    holdings, problems = {}, []
    for label, fn in (("上市", fetch_twse), ("上櫃", fetch_tpex)):
        try:
            got = fn(day, valid_stocks)
        except Exception as e:
            problems.append(f"{day} 外資持股{label}：下載失敗（{type(e).__name__}）")
            got = {}
        else:
            if not got:
                problems.append(f"{day} 外資持股{label}：沒有資料")
        holdings.update(got)
        time.sleep(SLEEP_BETWEEN_API)
    return holdings, problems


def merge_into(records, holdings):
    """把外資持股併進當天的三大法人資料（每日排程用）"""
    for r in records:
        shares, ratio = holdings.get(r["stock_id"], (None, None))
        r["foreign_hold_shares"], r["foreign_hold_ratio"] = shares, ratio
    return records


def _trading_days(start, end):
    """用大盤指數的日期當作交易日"""
    res = f.supabase.table("market_index").select("date").eq("index_code", "TAIEX") \
        .gte("date", start).lte("date", end).order("date").execute()
    return [r["date"] for r in res.data]


def _saved_stocks(day):
    """資料庫裡這一天已有三大法人的股票"""
    rows, start = [], 0
    while True:
        res = f.supabase.table(TABLE).select("stock_id").eq("date", day) \
            .order("stock_id").range(start, start + 999).execute()
        rows += res.data
        if len(res.data) < 1000:
            return {r["stock_id"] for r in rows}
        start += 1000


def fetch_range(start, end, valid_stocks, dry_run=False):
    """逐日補抓 start～end（YYYY-MM-DD）的外資持股。回傳 (寫入筆數, 問題清單)"""
    saved_total, problems = 0, []
    days = _trading_days(start, end)
    print(f"  共 {len(days)} 個交易日")
    for i, day in enumerate(days, 1):
        holdings, day_problems = fetch_day(day, valid_stocks)
        problems.extend(day_problems)
        if not holdings:
            continue
        saved = _saved_stocks(day)
        records = [{"stock_id": sid, "date": day, "foreign_hold_shares": s, "foreign_hold_ratio": r}
                   for sid, (s, r) in holdings.items() if sid in saved]
        print(f"  [{i}/{len(days)}] {day}：抓到 {len(holdings)} 檔，資料庫有三大法人的 {len(records)} 檔")
        if dry_run:
            if records:
                print(f"    例：{records[0]}")
            continue
        written, failed = f.save_data_batches(TABLE, records)
        saved_total += written
        if failed:
            problems.append(f"{day} {TABLE}：{failed} 筆寫入失敗")
    return saved_total, problems


if __name__ == "__main__":
    start = os.getenv("START_DATE", "").strip()
    end = os.getenv("END_DATE", "").strip() or start
    if not start:
        print("請設定 START_DATE（YYYY-MM-DD）")
        sys.exit(1)
    _, errs = fetch_range(start, end, f.get_valid_stock_ids(), dry_run=os.getenv("DRY_RUN") == "1")
    for err in errs:
        print(f"❌ {err}")
    sys.exit(1 if errs else 0)
