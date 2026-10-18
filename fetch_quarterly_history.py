"""
歷史季報 - 從公開資訊觀測站的彙總報表補抓過去季別

每天排程用的開放資料（fetch_stock_info.fetch_quarterly_report）只有最新一季；
公開資訊觀測站每季有全市場的綜合損益表（t163sb04）與資產負債表（t163sb05）彙總表，可以查過去的季別。
兩張表的欄位名稱和開放資料相同，逐列解析共用 fetch_stock_info 的 parse_income_row、parse_balance_row。
損益數字是年初累計、金額單位千元，與開放資料相同。

和每日排程不同的地方：
  - P/B 用該季最後一個交易日的收盤價計算（每日排程用抓取當下的收盤價）
  - 彙總表沒有出表日期，updated_at 留空

資料庫裡已經有的股票和季別會跳過，不會覆蓋每日排程抓到的資料。
backfill.py 用 DATASETS=quarterly 回補指定區間（季底落在區間內的季別）。
直接執行這個檔案並設 DRY_RUN=1、START_DATE、END_DATE，只會印出抓到的筆數和範例，不寫入資料庫。
"""
import os
import re
import sys
import time
from datetime import date, timedelta

import requests

import fetch_stock_info as f

TABLE = "quarterly_report"
URL = "https://mopsov.twse.com.tw/mops/web/ajax_{report}"
INCOME_REPORT = "t163sb04"
BALANCE_REPORT = "t163sb05"
MARKETS = ("sii", "otc")  # 上市、上櫃
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
SLEEP_BETWEEN_PAGE = 4
RETRIES = 3
QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}

# 一張網頁有好幾個表格（一般業、金控、證券、保險…欄位各不相同），每個表格第一列是欄位名稱
_TABLE = re.compile(r"<table[^>]*>(.*?)</table>", re.S | re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>|&nbsp;")


def _quarters(start, end):
    """季底日期落在 start～end 之間的季別，回傳 [(年, 季), ...]"""
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    out = []
    for y in range(s.year, e.year + 1):
        for q, (m, d) in QUARTER_END.items():
            if s <= date(y, m, d) <= e:
                out.append((y, q))
    return out


def _download(report, market, y, q):
    data = {"encodeURIComponent": 1, "step": 1, "firstin": 1, "off": 1, "isQuery": "Y",
            "TYPEK": market, "year": y - 1911, "season": f"{q:02d}"}
    for attempt in range(RETRIES):
        try:
            r = requests.post(URL.format(report=report), data=data, headers=HEADERS, timeout=90)
            r.raise_for_status()
            r.encoding = "utf-8"
            return r.text
        except Exception:
            if attempt == RETRIES - 1:
                raise
            time.sleep(15 * (attempt + 1))


def parse_page(html, valid_stocks):
    """把網頁裡每個表格轉成 {股票代號: {欄位名稱: 值}}"""
    rows_by_stock = {}
    for table in _TABLE.findall(html):
        header = None
        for tr in _ROW.findall(table):
            cells = [_TAG.sub("", c).strip() for c in _CELL.findall(tr)]
            if cells and cells[0] == "公司代號":
                header = cells
                continue
            if not header or not cells or cells[0] not in valid_stocks:
                continue
            rows_by_stock[cells[0]] = dict(zip(header, cells))
    return rows_by_stock


def _quarter_end_prices(y, q):
    """各股在季底前最後一次的收盤價（往前找 10 天內，停牌股用更早的價格）。
    股價表的索引是（股票, 日期），用日期區間查詢會全表掃描而逾時，所以逐日用「日期等於」查。"""
    m, d = QUARTER_END[q]
    end = date(y, m, d)
    prices = {}
    for back in range(11):
        day, start = (end - timedelta(days=back)).isoformat(), 0
        while True:
            res = f.supabase.table("daily_prices").select("stock_id, close_price").eq("date", day) \
                .order("stock_id").range(start, start + 999).execute()
            for row in res.data:
                if row["close_price"] is not None:
                    prices.setdefault(row["stock_id"], row["close_price"])
            if len(res.data) < 1000:
                break
            start += 1000
    return prices


def _already_saved(y, q):
    rows, start = [], 0
    while True:
        res = f.supabase.table(TABLE).select("stock_id").eq("year", y).eq("quarter", q) \
            .order("stock_id").range(start, start + 999).execute()
        rows += res.data
        if len(res.data) < 1000:
            return {r["stock_id"] for r in rows}
        start += 1000


def fetch_quarter(y, q, valid_stocks):
    """抓某一季全市場的損益表與資產負債表。回傳 (records, problems)，records 還沒有 P/B"""
    label = f"{y}Q{q}"
    income, balance, problems = {}, {}, []
    for report, target, name in ((INCOME_REPORT, income, "損益表"), (BALANCE_REPORT, balance, "資產負債表")):
        for market in MARKETS:
            try:
                rows = parse_page(_download(report, market, y, q), valid_stocks)
            except Exception as e:
                problems.append(f"{label} {name} {market}：下載失敗（{type(e).__name__}）")
                continue
            if not rows:
                problems.append(f"{label} {name} {market}：彙總表沒有資料")
            target.update(rows)
            time.sleep(SLEEP_BETWEEN_PAGE)

    records = []
    for stock_id, row in income.items():
        inc = f.parse_income_row(row)
        inc["year"], inc["quarter"] = y, q  # 彙總表沒有年度、季別欄位
        bal = f.parse_balance_row(balance[stock_id]) if stock_id in balance else {}
        records.append((stock_id, inc, bal))
    return records, problems


def fetch_range(start, end, valid_stocks, dry_run=False):
    """
    逐季補抓 start～end（YYYY-MM-DD）的季報，資料庫已有的股票跳過，每季抓完就寫入。
    回傳 (寫入筆數, 問題清單)
    """
    saved_total, problems = 0, []
    for y, q in _quarters(start, end):
        label = f"{y}Q{q}"
        parts, quarter_problems = fetch_quarter(y, q, valid_stocks)
        problems.extend(quarter_problems)
        if not parts:
            print(f"  {label}：沒有資料")
            continue
        prices = _quarter_end_prices(y, q)
        records = [f.build_quarterly_record(sid, inc, bal, prices.get(sid)) for sid, inc, bal in parts]
        if dry_run:
            print(f"  {label}：抓到 {len(records)} 筆（含資產負債表 {sum(bool(b) for _, _, b in parts)} 筆），"
                  f"例：{records[0]}")
            continue
        saved = _already_saved(y, q)
        todo = [r for r in records if r["stock_id"] not in saved]
        written, failed = f.save_data_batches(TABLE, todo)
        saved_total += written
        print(f"  {label}：抓到 {len(records)} 筆，已存在 {len(records) - len(todo)} 筆，寫入 {written} 筆")
        if failed:
            problems.append(f"{label} {TABLE}：{failed} 筆寫入失敗")
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
