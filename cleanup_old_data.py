"""
清除過期資料 - 每天評分完後執行，讓資料庫維持在 Supabase 免費額度 500 MB 內

各表保留期限（2026-10 決定）：
  股價 daily_prices                     3 年（長期均線、還原權值、回測）
  三大法人＋外資持股 stock_ins_daily      2 年（籌碼因子驗證）
  融資融券 margin_utilization             1 年（網頁最多看 60 天；券資比只占評分 8 分，2026-10 從 2 年縮短，省約 50 MB）
  大戶持股 holder_distribution            2 年
  每日評分 daily_signals                  1 年（規則回測）
  其他小表（月營收、季報、指數、期貨、還原權值事件）全部保留。

做法：用權值股 2330 找出過期的日期，一天一天刪（依日期刪除走主鍵索引，很快），
每張表每次最多刪 MAX_DAYS_PER_RUN 天。平常每天只會刪到剛過期的一天；
排程停了很久也會分幾天慢慢刪完，不會一次刪太多而逾時。
刪除後空間會由資料庫自動重複使用，不需要手動處理。
刪除前會先把那一天的資料封存成壓縮檔放到 Supabase Storage（見 archive.py），確認封存成功才刪除，
所以舊資料不會遺失，需要時可以用 archive.load_archive 讀回來。
設 DRY_RUN=1 只印出會刪除的筆數，不刪除；再設 CLEANUP_DATE（YYYY-MM-DD）可以假設今天是那一天，
用來預先確認之後會刪到哪些資料。
設 ARCHIVE_ONLY=1 只封存、不刪除（可搭配 CLEANUP_DATE 預先封存之後才會過期的資料）。
"""
import os
import sys
from datetime import date, datetime, timezone

from supabase import create_client
from dotenv import load_dotenv

from archive import archive_day
from retry import execute as _execute

load_dotenv()

# (資料表, 日期欄位, 保留年數)
RETENTION = [
    ("daily_prices", "date", 3),
    ("stock_ins_daily", "date", 2),
    ("margin_utilization", "date", 1),
    ("holder_distribution", "week_date", 2),
    ("daily_signals", "date", 1),
]
ANCHOR_STOCK = "2330"   # 每天都有交易，用它的主鍵索引找過期的日期比較快
MAX_DAYS_PER_RUN = 10   # 每張表每次最多刪這麼多天


def years_ago(day, years):
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 2/29
        return day.replace(year=day.year - years, day=28)


def cleanup(sb, table, col, years, today, dry_run, archive_only=False):
    """table 超過 years 年的資料先封存、再刪除，一次處理一天。回傳刪除（或封存）筆數"""
    cutoff = years_ago(today, years).isoformat()
    days = [r[col] for r in _execute(
        lambda: sb.table(table).select(col).eq("stock_id", ANCHOR_STOCK)
        .lt(col, cutoff).order(col, desc=False).limit(MAX_DAYS_PER_RUN)).data]
    total = 0
    for d in days:
        if dry_run:
            res = _execute(lambda d=d: sb.table(table).select(col, count="exact", head=True).eq(col, d))
            total += res.count or 0
            continue
        archived = archive_day(sb, table, col, d)  # 封存失敗會丟出錯誤，這天就不會被刪
        if archive_only:
            total += archived
            continue
        res = _execute(lambda d=d: sb.table(table).delete(count="exact", returning="minimal").eq(col, d))
        total += res.count or 0
    action = "會刪除" if dry_run else "封存" if archive_only else "封存並刪除"
    span = f"{days[0]}～{days[-1]}（{len(days)} 天）" if days else "沒有過期資料"
    print(f"  {table}：保留 {years} 年（{cutoff} 起），{span}，{action} {total:,} 筆")
    return total


def main():
    dry_run = os.getenv("DRY_RUN") == "1"
    archive_only = os.getenv("ARCHIVE_ONLY") == "1"
    override = os.getenv("CLEANUP_DATE", "").strip()
    today = date.fromisoformat(override) if override else datetime.now(timezone.utc).date()
    print(f"========== 清除過期資料：{today}{'（只檢查，不刪除）' if dry_run else '（只封存，不刪除）' if archive_only else ''} ==========")
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
    failed = 0
    for table, col, years in RETENTION:
        try:
            cleanup(sb, table, col, years, today, dry_run, archive_only)
        except Exception as e:
            print(f"❌ {table} 清除失敗：{e}")
            failed += 1
    if failed:
        return 1
    print("✅ 清除完畢")
    return 0


if __name__ == "__main__":
    sys.exit(main())
