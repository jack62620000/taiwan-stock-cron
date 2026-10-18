"""
現金流量 - 從公開資訊觀測站的現金流量表彙總表（t163sb20）補抓每季的營業、投資、籌資現金流

公開資訊觀測站每季有全市場的現金流量表彙總表，可以查過去的季別，一次一個市場。
金額單位千元、年初累計，與季報其他欄位相同。
自由現金流＝營業現金流＋投資現金流（彙總表沒有資本支出明細，採常見的簡化算法）。

只更新 quarterly_report 已有的股票和季別的 4 個現金流欄位，不動其他欄位；重抓會覆蓋成相同數值。
backfill.py 用 DATASETS=cashflow 回補指定區間（季底落在區間內的季別）。
直接執行這個檔案並設 DRY_RUN=1、START_DATE、END_DATE，只會印出抓到的筆數和範例，不寫入資料庫。
"""
import os
import sys
import time

import fetch_quarterly_history as qh
import fetch_stock_info as f

TABLE = "quarterly_report"
REPORT = "t163sb20"
OPERATING = "營業活動之淨現金流入（流出）"
INVESTING = "投資活動之淨現金流入（流出）"
FINANCING = "籌資活動之淨現金流入（流出）"


def _amount(val):
    v = f.parse_float(val)
    return int(v) if v is not None else None


def fetch_quarter(y, q, valid_stocks):
    """抓某一季上市＋上櫃的現金流量。回傳 ({股票代號: 現金流欄位}, 問題清單)"""
    rows, problems = {}, []
    for market in qh.MARKETS:
        try:
            got = qh.parse_page(qh._download(REPORT, market, y, q), valid_stocks)
        except Exception as e:
            problems.append(f"{y}Q{q} 現金流量表 {market}：下載失敗（{type(e).__name__}）")
            continue
        if not got:
            problems.append(f"{y}Q{q} 現金流量表 {market}：彙總表沒有資料")
        rows.update(got)
        time.sleep(qh.SLEEP_BETWEEN_PAGE)

    cash = {}
    for stock_id, row in rows.items():
        op, inv, fin = _amount(row.get(OPERATING)), _amount(row.get(INVESTING)), _amount(row.get(FINANCING))
        cash[stock_id] = {
            "operating_cf": op, "investing_cf": inv, "financing_cf": fin,
            "free_cf": op + inv if op is not None and inv is not None else None,
        }
    return cash, problems


def fetch_range(start, end, valid_stocks, dry_run=False):
    """逐季補抓 start～end（YYYY-MM-DD）的現金流量，只更新資料庫已有的季報。回傳 (寫入筆數, 問題清單)"""
    saved_total, problems = 0, []
    for y, q in qh._quarters(start, end):
        label = f"{y}Q{q}"
        cash, quarter_problems = fetch_quarter(y, q, valid_stocks)
        problems.extend(quarter_problems)
        if not cash:
            print(f"  {label}：沒有資料")
            continue
        saved = qh._already_saved(y, q)
        records = [{"stock_id": sid, "year": y, "quarter": q, **values}
                   for sid, values in cash.items() if sid in saved]
        print(f"  {label}：抓到 {len(cash)} 檔，資料庫有季報的 {len(records)} 檔")
        if dry_run:
            if records:
                print(f"    例：{records[0]}")
            continue
        written, failed = f.save_data_batches(TABLE, records)
        saved_total += written
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
    for err in errs:
        print(f"❌ {err}")
    sys.exit(1 if errs else 0)
