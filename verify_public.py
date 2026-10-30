"""
對照公開來源的抽查 - 給 GitHub Actions 每週排程使用（.github/workflows/verify_public.yml）

每次隨機抽最近 LOOKBACK_DAYS 天內的 SAMPLE_DAYS 個交易日，用排程本身的抓取函式重新向證交所、櫃買中心下載
當天的原始資料，逐筆逐欄和資料庫比對：
  台股主資料庫：股價（daily_prices）、三大法人（stock_ins_daily）、融資融券（margin_utilization）、最新一個月營收
  ETF 資料庫：ETF 價量（etf_daily_prices）、三大法人（etf_ins_daily）、融資融券（etf_margin_daily）
找出「來源有、資料庫沒有」「資料庫有、來源沒有」「數字不同」三種差異。
來源下載失敗或當天回傳空資料時只記一筆提醒，不算差異（可能被擋或來源暫時沒資料）。

和每日的 check_data.py 不同：check_data 只看筆數，這裡看每個數字是不是跟官方一致，
能抓到寫入時欄位錯位、後來被別的程式覆蓋、漏寫幾檔之類筆數看不出來的問題。
注意：用的是排程同一套解析程式，解析邏輯本身的錯誤要靠 tests/ 的範例回應測試。

有差異時以錯誤碼結束，GitHub 會把這次排程標成失敗並寄信。
其他設定：VERIFY_DAYS（YYYY-MM-DD，逗號分隔）指定要抽查的日期；VERIFY_SKIP_ETF=1 不查 ETF 資料庫。
"""
import os
import random
import sys
import time
from datetime import date, timedelta

import fetch_stock_info as f

SAMPLE_DAYS = 3
LOOKBACK_DAYS = 60
SKIP_RECENT_DAYS = 2      # 最近兩天可能還在補抓，不抽
SLEEP_BETWEEN = 5         # 證交所連續請求太快會被擋
REL_TOL = 1e-6
MAX_PRINT = 20            # 每一類差異最多印出幾筆
REVENUE_COLS = {"revenue", "cumulative_revenue"}

# (資料表, 名稱, 上市抓取, 上櫃抓取)；抓取函式失敗時回傳空清單
STOCK_DATASETS = [
    ("daily_prices", "股價", f.fetch_twse_daily_all, f.fetch_tpex_otc_daily),
    ("stock_ins_daily", "三大法人", f.fetch_twse_stock_ins, f.fetch_tpex_stock_ins),
    ("margin_utilization", "融資融券", f.fetch_twse_margin, f.fetch_tpex_margin),
]


def same(a, b):
    """兩個欄位值是否相同：數字容許極小誤差，空值只等於空值"""
    if a is None or b is None:
        return a is None and b is None
    try:
        x, y = float(a), float(b)
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()
    return abs(x - y) <= REL_TOL * max(1.0, abs(x), abs(y))


def compare(source, db_rows, keys, cols=None):
    """
    逐筆比對。只比來源有提供的欄位（資料庫可能另有其他程式補的欄位，例如外資持股），cols 可再限定欄位。
    回傳 (來源有資料庫沒有, 資料庫有來源沒有, [(key, 欄位, 來源值, 資料庫值)])
    """
    src = {tuple(str(r[k]) for k in keys): r for r in source}
    db = {tuple(str(r[k]) for k in keys): r for r in db_rows}
    missing = sorted(set(src) - set(db))
    extra = sorted(set(db) - set(src))
    diffs = []
    for k in sorted(set(src) & set(db)):
        for col, val in src[k].items():
            if col in keys or (cols and col not in cols):
                continue
            if not same(val, db[k].get(col)):
                diffs.append((k, col, val, db[k].get(col)))
    return missing, extra, diffs


def report(label, missing, extra, diffs):
    """印出差異，回傳差異總數"""
    n = len(missing) + len(extra) + len(diffs)
    print(f"  {label}：{'✅ 一致' if not n else f'❌ 缺 {len(missing)}、多 {len(extra)}、數字不同 {len(diffs)}'}")
    for title, items in (("來源有、資料庫沒有", missing), ("資料庫有、來源沒有", extra)):
        if items:
            print(f"    {title}：{'、'.join('/'.join(k) for k in items[:MAX_PRINT])}"
                  f"{' …' if len(items) > MAX_PRINT else ''}")
    for k, col, src, dbv in diffs[:MAX_PRINT]:
        print(f"    {'/'.join(k)} {col}：來源 {src}，資料庫 {dbv}")
    return n


def select_day(client, table, day, columns="*", col="date"):
    rows, start = [], 0
    while True:
        res = client.table(table).select(columns).eq(col, day).range(start, start + 999).execute()
        rows += res.data
        if len(res.data) < 1000:
            return rows
        start += 1000


def pick_days(today=None):
    override = os.getenv("VERIFY_DAYS", "").strip()
    if override:
        return [d.strip() for d in override.split(",") if d.strip()]
    today = today or date.today()
    start = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    end = (today - timedelta(days=SKIP_RECENT_DAYS)).isoformat()
    days = f.get_trading_days(start, end)
    return sorted(random.sample(days, min(SAMPLE_DAYS, len(days))))


def verify_stock_day(day, valid_stocks, notes):
    total = 0
    for table, label, fetch_twse, fetch_tpex in STOCK_DATASETS:
        source = []
        for market, fn in (("上市", fetch_twse), ("上櫃", fetch_tpex)):
            got = fn(day, valid_stocks)
            time.sleep(SLEEP_BETWEEN)
            if not got:
                notes.append(f"{day} {market}{label}：來源沒有回傳資料，略過")
                break
            source += got
        else:
            db_rows = select_day(f.supabase, table, day)
            # 只比對目前清單裡的股票（之後下市的股票來源已經沒有）
            db_rows = [r for r in db_rows if r["stock_id"] in valid_stocks]
            total += report(f"{day} {label}", *compare(source, db_rows, ("stock_id", "date")))
    return total


def verify_revenue(valid_stocks, notes):
    source = f.fetch_monthly_revenue(valid_stocks)
    if not source:
        notes.append("月營收：來源沒有回傳資料，略過")
        return 0
    month = max(r["year_month"] for r in source)
    source = [r for r in source if r["year_month"] == month]
    db_rows = select_day(f.supabase, "monthly_revenue", month, col="year_month")
    # 開放資料只有已公布的公司；資料庫多的是之後才用其他來源補上的，不算差異。
    # 備註、出表日期等文字欄位可能被歷史回補（公開資訊觀測站）改寫，只比營收數字
    missing, _, diffs = compare(source, db_rows, ("stock_id", "year_month"), cols=REVENUE_COLS)
    return report(f"{month} 月營收", missing, [], diffs)


def verify_etf_day(day, notes):
    import etf_fetch as e
    total = 0
    try:
        prices = e.fetch_twse_prices(day)
        time.sleep(SLEEP_BETWEEN)
        prices += e.parse_tpex_prices(e.fetch_tpex_price_rows(day), day)
        time.sleep(SLEEP_BETWEEN)
        ins = e.fetch_twse_ins(day)
        time.sleep(SLEEP_BETWEEN)
        ins += e.fetch_tpex_ins(day)
        time.sleep(SLEEP_BETWEEN)
        margin = e.fetch_margin(day)
        time.sleep(SLEEP_BETWEEN)
    except Exception as ex:
        notes.append(f"{day} ETF：來源下載失敗（{ex}），略過")
        return 0
    for table, label, source in (("etf_daily_prices", "ETF 價量", prices), ("etf_ins_daily", "ETF 法人", ins),
                                 ("etf_margin_daily", "ETF 融資融券", margin)):
        if not source:
            notes.append(f"{day} {label}：來源沒有回傳資料，略過")
            continue
        total += report(f"{day} {label}", *compare(source, select_day(e.db(), table, day), ("etf_id", "date")))
    return total


def main():
    days = pick_days()
    print(f"========== 對照公開來源抽查：{'、'.join(days)} ==========")
    valid_stocks = f.get_valid_stock_ids()
    if not valid_stocks:
        print("❌ 無法取得股票清單")
        return 1
    notes, total = [], 0
    for day in days:
        print(f"\n--- {day} 台股 ---")
        total += verify_stock_day(day, valid_stocks, notes)
        if os.getenv("VERIFY_SKIP_ETF") != "1":
            print(f"--- {day} ETF ---")
            total += verify_etf_day(day, notes)
    print("\n--- 月營收 ---")
    total += verify_revenue(valid_stocks, notes)
    for n in notes:
        print(f"ℹ️ {n}")
    if total:
        print(f"\n❌ 共 {total} 筆和公開來源不一致，請看上面的明細")
        return 1
    print("\n✅ 抽查的資料都和公開來源一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
