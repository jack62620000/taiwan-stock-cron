"""
每日資料檢查 - 排程評分完後執行，檢查最近幾個交易日的資料筆數是否正常

做法：取最近 CHECK_WINDOW 個交易日（以 2330 有股價的日子為準），計算每張表每天的筆數，
以這幾天的中位數為基準；最近 CHECK_RECENT 天裡任一天少於基準的 MIN_RATIO（90%），就以錯誤碼結束，
GitHub 會把這次排程標成失敗（紅色），點進去就看得到哪天、哪張表少了多少。

平常每天約有：股價 1,970 檔、三大法人 1,850 檔、融資融券 1,860 檔、評分 1,950 檔。

另外檢查資料庫用量（database_size 函式，見 sql/functions.sql）：超過 SIZE_WARN_MB 也標成失敗，
提醒要縮短保留期限（cleanup_old_data.py）或升級方案，免得超過 Supabase 免費額度 500 MB 後無法寫入。

用法：python check_data.py
"""
import os
import statistics
import sys
import time

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

TABLES = [
    ("daily_prices", "股價"),
    ("stock_ins_daily", "三大法人"),
    ("margin_utilization", "融資融券"),
    ("daily_signals", "每日評分"),
]
ANCHOR_STOCK = "2330"
CHECK_WINDOW = 6   # 拿最近 6 個交易日算基準
CHECK_RECENT = 3   # 只檢查最近 3 天（更早的已經檢查過）
MIN_RATIO = 0.9
SIZE_LIMIT_MB = 500  # Supabase 免費額度
SIZE_WARN_MB = 450   # 2026-10 估計各表存滿保留期限後約 462 MB，超過 450 就要開始處理


def _execute(make_request, retries=4):
    for attempt in range(1, retries + 1):
        try:
            return make_request().execute()
        except Exception as e:
            if attempt == retries:
                raise
            print(f"  連線錯誤，{3 * attempt} 秒後重試：{type(e).__name__}")
            time.sleep(3 * attempt)


def recent_trading_days(sb, n=CHECK_WINDOW):
    rows = _execute(lambda: sb.table("daily_prices").select("date").eq("stock_id", ANCHOR_STOCK)
                    .order("date", desc=True).limit(n)).data
    return sorted(r["date"] for r in rows)


def count_rows(sb, table, day):
    return _execute(lambda: sb.table(table).select("stock_id", count="exact", head=True).eq("date", day)).count or 0


def check(sb):
    """回傳 (表格文字, 問題清單)"""
    days = recent_trading_days(sb)
    if not days:
        return "", ["daily_prices 找不到任何交易日"]
    lines = ["日期        " + "".join(f"{label:>8}" for _, label in TABLES)]
    counts = {t: {d: count_rows(sb, t, d) for d in days} for t, _ in TABLES}
    for d in days:
        lines.append(d + "  " + "".join(f"{counts[t][d]:>10,}" for t, _ in TABLES))
    problems = []
    for table, label in TABLES:
        base = statistics.median(counts[table].values())
        for d in days[-CHECK_RECENT:]:
            n = counts[table][d]
            if n < base * MIN_RATIO:
                problems.append(f"{d} {label}（{table}）只有 {n:,} 筆，平常約 {base:,.0f} 筆")
    return "\n".join(lines), problems


def check_size(sb):
    """回傳 (說明文字, 問題清單)；讀不到用量時只印提醒，不算失敗"""
    try:
        size = _execute(lambda: sb.rpc("database_size"))
    except Exception as e:
        return f"資料庫用量：讀取失敗（{type(e).__name__}），請確認已建立 database_size 函式", []
    mb = (size.data or 0) / 1024 / 1024
    text = f"資料庫用量：{mb:,.0f} MB / {SIZE_LIMIT_MB} MB（{mb / SIZE_LIMIT_MB:.0%}）"
    if mb >= SIZE_WARN_MB:
        return text, [f"資料庫已用 {mb:,.0f} MB，超過提醒門檻 {SIZE_WARN_MB} MB（上限 {SIZE_LIMIT_MB} MB），"
                      "請縮短 cleanup_old_data.py 的保留期限或升級方案"]
    return text, []


def main():
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
    print("========== 每日資料檢查 ==========")
    table, problems = check(sb)
    print(table)
    size_text, size_problems = check_size(sb)
    print("\n" + size_text)
    if problems:
        print("\n❌ 資料筆數異常：")
        for p in problems:
            print(f"   - {p}")
            if os.getenv("GITHUB_ACTIONS"):
                print(f"::error title=資料筆數異常::{p}")
        print("   可到 Actions 手動執行 daily_update，在 target_date 填入該日期補抓。")
    if size_problems:
        print("\n❌ 資料庫容量：")
        for p in size_problems:
            print(f"   - {p}")
            if os.getenv("GITHUB_ACTIONS"):
                print(f"::error title=資料庫容量接近上限::{p}")
    if problems or size_problems:
        return 1
    print("\n✅ 最近幾天的資料筆數、資料庫用量都正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
