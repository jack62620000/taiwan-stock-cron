"""
歷史期貨三大法人 - 從期交所「三大法人－區分各期貨契約」下載補抓過去日期

每天排程用的期交所開放資料（auto_fetch_futures.py）只有最近一個交易日；
期交所的下載網頁可以一次查一段區間，欄位與開放資料相同（口數、契約金額千元），
2026-04 的資料與每日排程存下來的逐欄比對完全一致。

只抓臺股期貨，寫入 futures_institutional（同一天重抓會覆蓋成相同數值）。
backfill.py 用 DATASETS=futures 回補指定區間。
直接執行這個檔案並設 DRY_RUN=1、START_DATE、END_DATE，只會印出抓到的筆數和範例，不寫入資料庫。
"""
import csv
import io
import os
import sys
import time
from datetime import date

import requests

import fetch_stock_info as f

TABLE = "futures_institutional"
URL = "https://www.taifex.com.tw/cht/3/futContractsDateDown"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
SLEEP_BETWEEN_REQUEST = 5
RETRIES = 3

# 下載檔欄位 → 資料表欄位
COLUMNS = {
    "多方未平倉口數": "long_oi",
    "多方未平倉契約金額(千元)": "long_oi_value",
    "空方未平倉口數": "short_oi",
    "空方未平倉契約金額(千元)": "short_oi_value",
    "多空未平倉口數淨額": "net_oi",
    "多空未平倉契約金額淨額(千元)": "net_oi_value",
}


def _years(start, end):
    """區間切成一年一段，避免一次查太長"""
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    for y in range(s.year, e.year + 1):
        yield max(s, date(y, 1, 1)), min(e, date(y, 12, 31))


def _download(s, e):
    data = {"queryStartDate": f"{s:%Y/%m/%d}", "queryEndDate": f"{e:%Y/%m/%d}", "commodityId": "TXF"}
    for attempt in range(RETRIES):
        try:
            r = requests.post(URL, data=data, headers=HEADERS, timeout=90)
            r.raise_for_status()
            return r.content.decode("big5", errors="replace")
        except Exception:
            if attempt == RETRIES - 1:
                raise
            time.sleep(15 * (attempt + 1))


def parse_csv(text):
    records = []
    for row in csv.DictReader(io.StringIO(text)):
        day = (row.get("日期") or "").strip()
        if (row.get("商品名稱") or "").strip() != "臺股期貨" or len(day) != 10:
            continue
        records.append({
            "date": day.replace("/", "-"),
            "contract_code": "臺股期貨",
            "item": (row.get("身份別") or "").strip(),
            **{col: f.parse_volume(row.get(src)) for src, col in COLUMNS.items()},
        })
    return records


def fetch_range(start, end, dry_run=False):
    """逐年抓 start～end（YYYY-MM-DD）的期貨三大法人並寫入。回傳 (寫入筆數, 問題清單)"""
    saved_total, problems = 0, []
    for s, e in _years(start, end):
        try:
            records = parse_csv(_download(s, e))
        except Exception as ex:
            problems.append(f"{s}～{e} 期貨三大法人：下載失敗（{type(ex).__name__}）")
            continue
        days = len({r["date"] for r in records})
        print(f"  {s}～{e}：{days} 個交易日、{len(records)} 筆")
        if not records:
            problems.append(f"{s}～{e} 期貨三大法人：沒有資料")
        elif dry_run:
            print(f"    例：{records[0]}")
        else:
            written, failed = f.save_data_batches(TABLE, records)
            saved_total += written
            if failed:
                problems.append(f"{s}～{e} {TABLE}：{failed} 筆寫入失敗")
        time.sleep(SLEEP_BETWEEN_REQUEST)
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
