"""
歷史月營收 - 從公開資訊觀測站的每月彙總表補抓過去月份

每天排程用的開放資料（fetch_stock_info.fetch_monthly_revenue）只有最新一個月；
公開資訊觀測站每個月有一張全市場彙總表（上市、上櫃各分國內、國外公司兩張），可以查過去的月份。
金額單位都是千元，與開放資料相同。

資料庫裡已經有的股票和月份會跳過，不會覆蓋每日排程抓到的資料。
backfill.py 用 DATASETS=revenue 回補指定區間（以月份為單位）。
直接執行這個檔案並設 DRY_RUN=1、START_DATE、END_DATE，只會印出抓到的筆數和範例，不寫入資料庫。
"""
import os
import re
import sys
import time

import requests

import fetch_stock_info as f

TABLE = "monthly_revenue"
URL = "https://mopsov.twse.com.tw/nas/t21/{market}/t21sc03_{roc_year}_{month}_{kind}.html"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
# 上市、上櫃 × 國內公司（0）、國外公司（1）
PAGES = [("sii", "0"), ("sii", "1"), ("otc", "0"), ("otc", "1")]
SLEEP_BETWEEN_PAGE = 3
RETRIES = 3

# 表格標籤大小寫不一（例如「去年同月增減」欄位是 <Td>），一律不分大小寫
_ROW = re.compile(r"<tr[^>]*>\s*<td[^>]*>\s*(\d{4,6})\s*</td>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>|&nbsp;")


def _int(s):
    try:
        return int(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _growth(now, before):
    """成長率 %，算不出來（缺值或基期為 0、負數）回傳 None"""
    if now is None or not before or before <= 0:
        return None
    return round((now / before - 1) * 100, 2)


def _months(start, end):
    y, m = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    while (y, m) <= (ey, em):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _download(url):
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 404:
                return None  # 該月份還沒公布或沒有這張表
            r.raise_for_status()
            r.encoding = "big5"
            return r.text
        except Exception:
            if attempt == RETRIES - 1:
                raise
            time.sleep(10 * (attempt + 1))


def parse_page(html, year_month, valid_stocks):
    """
    彙總表每列：公司代號、名稱、當月營收、上月營收、去年當月營收、上月比較%、去年同月%、
    當月累計、去年累計、前期比較%、備註。成長率一律用金額自己算，不依賴表上的百分比欄位。
    觀測站的歷史表是更正後的數字；與每日開放資料比對（2026-08），1,976 檔中只有 2 檔不同，
    都是排除停業單位營收的公司。
    """
    records = []
    for sid, rest in _ROW.findall(html):
        if sid not in valid_stocks:
            continue
        cells = [_TAG.sub("", c).strip() for c in _CELL.findall(rest)]
        if len(cells) != 10:
            continue
        _, revenue, prev, last_year, _, _, cum, last_cum, _, note = cells
        revenue, prev, last_year, cum, last_cum = (_int(x) for x in (revenue, prev, last_year, cum, last_cum))
        note = note.strip("-").strip() or None
        records.append({
            "stock_id": sid,
            "year_month": year_month,
            "revenue": revenue,
            "mom_growth": _growth(revenue, prev),
            "yoy_growth": _growth(revenue, last_year),
            "cumulative_revenue": cum,
            "prev_month_revenue": prev,
            "last_year_revenue": last_year,
            "last_year_cumulative_revenue": last_cum,
            "cumulative_yoy_growth": _growth(cum, last_cum),
            "note": note,
        })
    return records


def months_already_saved(year_month):
    rows, start = [], 0
    while True:
        res = f.supabase.table(TABLE).select("stock_id").eq("year_month", year_month) \
            .order("stock_id").range(start, start + 999).execute()
        rows += res.data
        if len(res.data) < 1000:
            return {r["stock_id"] for r in rows}
        start += 1000


def fetch_month(y, m, valid_stocks):
    """抓某一個月份全市場的營收。回傳 (records, problems)"""
    year_month = f"{y:04d}-{m:02d}"
    records, problems = [], []
    for market, kind in PAGES:
        url = URL.format(market=market, roc_year=y - 1911, month=m, kind=kind)
        try:
            html = _download(url)
        except Exception as e:
            problems.append(f"{year_month} 月營收 {market}_{kind}：下載失敗（{type(e).__name__}）")
            continue
        if html:
            records.extend(parse_page(html, year_month, valid_stocks))
        time.sleep(SLEEP_BETWEEN_PAGE)
    return records, problems


def fetch_range(start, end, valid_stocks, dry_run=False):
    """
    逐月補抓 start～end（YYYY-MM-DD，只看年月）的月營收，資料庫已有的股票跳過，每月抓完就寫入。
    回傳 (寫入筆數, 問題清單)
    """
    saved_total, problems = 0, []
    for y, m in _months(start, end):
        year_month = f"{y:04d}-{m:02d}"
        records, month_problems = fetch_month(y, m, valid_stocks)
        problems.extend(month_problems)
        if not records:
            print(f"  {year_month}：沒有資料")
            if not month_problems:
                problems.append(f"{year_month} 月營收：彙總表沒有資料")
            continue
        if dry_run:
            print(f"  {year_month}：抓到 {len(records)} 筆，例：{records[0]}")
            continue
        saved = months_already_saved(year_month)
        todo = [r for r in records if r["stock_id"] not in saved]
        written, failed = f.save_data_batches(TABLE, todo)
        saved_total += written
        print(f"  {year_month}：抓到 {len(records)} 筆，已存在 {len(records) - len(todo)} 筆，寫入 {written} 筆")
        if failed:
            problems.append(f"{year_month} {TABLE}：{failed} 筆寫入失敗")
    return saved_total, problems


if __name__ == "__main__":
    start = os.getenv("START_DATE", "").strip()
    end = os.getenv("END_DATE", "").strip() or start
    if not start:
        print("請設定 START_DATE（YYYY-MM-DD）")
        sys.exit(1)
    _, errs = fetch_range(start, end, f.get_valid_stock_ids(), dry_run=os.getenv("DRY_RUN") == "1")
    for e in errs:
        print(f"❌ {e}")
    sys.exit(1 if errs else 0)
