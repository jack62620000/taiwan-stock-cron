"""
大盤指數 - 加權指數（TAIEX）與櫃買指數（TPEX）每日行情

資料來源（2026-10 實測，都是一次查一個月）：
  加權指數  開高低收：證交所 MI_5MINS_HIST      漲跌點數、成交金額：證交所 FMTQIK
  櫃買指數  開高低收、漲跌：櫃買中心 indexInfo/inx   成交金額：櫃買中心 tradingIndex（千元，存成元）

auto_fetch.py 每天重抓當月；backfill.py 用 DATASETS=index 回補指定區間。
直接執行這個檔案並設 DRY_RUN=1、START_DATE、END_DATE，只會印出抓到的筆數和範例，不寫入資料庫。
"""
import os
import sys
import time
from datetime import date

import requests

import fetch_stock_info as f

TABLE = "market_index"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
SLEEP_BETWEEN_API = 3
RETRIES = 3


def _months(start, end):
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    y, m = s.year, s.month
    while (y, m) <= (e.year, e.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _get_json(url):
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == RETRIES - 1:
                raise
            # 被暫時擋下（403、429）要等久一點，其他錯誤 10、20 秒
            blocked = getattr(getattr(e, "response", None), "status_code", None) in f.BLOCKED_STATUS
            wait = f.BLOCKED_WAIT * (attempt + 1) if blocked else 10 * (attempt + 1)
            print(f"  下載失敗（{type(e).__name__}），{wait} 秒後重試...")
            time.sleep(wait)


def _day(s):
    """'113/01/02' 或 '2024/01/02' → '2024-01-02'"""
    y, m, d = str(s).strip().split("/")
    y = int(y) + 1911 if len(y) == 3 else int(y)
    return f"{y:04d}-{int(m):02d}-{int(d):02d}"


def _rows(data):
    """證交所的資料在 data，櫃買中心的在 tables[0].data"""
    if "tables" in data:
        return data["tables"][0].get("data", []) if data["tables"] else []
    return data.get("data", []) if data.get("stat") == "OK" else []


def fetch_twse(y, m):
    ym = f"{y:04d}{m:02d}01"
    ohlc = _rows(_get_json(f"https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST?date={ym}&response=json"))
    time.sleep(SLEEP_BETWEEN_API)
    summary = _rows(_get_json(f"https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={ym}&response=json"))
    time.sleep(SLEEP_BETWEEN_API)
    # FMTQIK：日期、成交股數、成交金額、成交筆數、加權指數、漲跌點數
    extra = {_day(r[0]): (f.parse_float(r[5]), f.parse_float(r[2])) for r in summary}
    records = []
    for r in ohlc:  # 日期、開盤、最高、最低、收盤
        day = _day(r[0])
        change, value = extra.get(day, (None, None))
        records.append({
            "index_code": "TAIEX", "date": day,
            "open": f.parse_float(r[1]), "high": f.parse_float(r[2]),
            "low": f.parse_float(r[3]), "close": f.parse_float(r[4]),
            "change": change, "trade_value": int(value) if value is not None else None,
        })
    return records


def fetch_tpex(y, m):
    ym = f"{y:04d}/{m:02d}/01"
    ohlc = _rows(_get_json(f"https://www.tpex.org.tw/www/zh-tw/indexInfo/inx?date={ym}&response=json"))
    time.sleep(SLEEP_BETWEEN_API)
    summary = _rows(_get_json(f"https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingIndex?date={ym}&response=json"))
    time.sleep(SLEEP_BETWEEN_API)
    # tradingIndex：日期、成交股數（仟股）、金額（仟元）、筆數、櫃買指數、漲跌
    value = {_day(r[0]): f.parse_float(r[2]) for r in summary}
    records = []
    for r in ohlc:  # 日期、開市、最高、最低、收市、漲跌
        day = _day(r[0])
        thousand = value.get(day)
        records.append({
            "index_code": "TPEX", "date": day,
            "open": f.parse_float(r[1]), "high": f.parse_float(r[2]),
            "low": f.parse_float(r[3]), "close": f.parse_float(r[4]),
            "change": f.parse_float(r[5]),
            "trade_value": int(thousand * 1000) if thousand is not None else None,
        })
    return records


def fetch_range(start, end, dry_run=False):
    """逐月抓 start～end（YYYY-MM-DD）的大盤指數並寫入。回傳 (寫入筆數, 問題清單)"""
    saved_total, problems = 0, []
    for y, m in _months(start, end):
        records = []
        for label, fn in (("加權指數", fetch_twse), ("櫃買指數", fetch_tpex)):
            try:
                got = [r for r in fn(y, m) if start <= r["date"] <= end and r["close"] is not None]
            except Exception as ex:
                problems.append(f"{y}-{m:02d} {label}：下載失敗（{type(ex).__name__}）")
                continue
            records.extend(got)
        print(f"  {y}-{m:02d}：{len(records)} 筆")
        if dry_run:
            if records:
                print(f"    例：{records[0]}")
            continue
        written, failed = f.save_data_batches(TABLE, records)
        saved_total += written
        if failed:
            problems.append(f"{y}-{m:02d} {TABLE}：{failed} 筆寫入失敗")
    return saved_total, problems


if __name__ == "__main__":
    start = os.getenv("START_DATE", "").strip()
    end = os.getenv("END_DATE", "").strip() or start
    if not start:
        print("請設定 START_DATE（YYYY-MM-DD）")
        sys.exit(1)
    _, errs = fetch_range(start, end, dry_run=os.getenv("DRY_RUN") == "1")
    for err in errs:
        print(f"❌ {err}")
    sys.exit(1 if errs else 0)
