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

資料庫快滿時自動縮短保留期限（SPACE_TIERS）：每次執行先讀資料庫用量（database_size 函式），
超過門檻就把最大的幾張表改用較短的期限，一樣先封存再刪除，所以資料不會遺失，只是網頁看不到更早的部分。
注意刪除後資料庫檔案不會變小（空出的空間由之後的新資料重複使用），所以用量會停在門檻附近，
縮短的期限會一直維持；之後升級方案或手動 VACUUM FULL 讓用量降下來，就會自動恢復原本的期限
（已刪除的舊資料不會自動補回，要用 archive.load_archive 讀回）。
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
# 資料庫快滿時改用的較短期限（月）：(用量門檻 MB, {資料表: 保留月數})，由高到低排，符合第一個就用它。
# 2026-10 估計各表存滿原本期限約 462 MB；95%（475 MB）先縮股價、法人、大戶，約可空出 45 MB；
# 98%（490 MB）再縮一次。融資券、評分本來就只留 1 年，不再縮
SPACE_TIERS = [
    (490, {"daily_prices": 24, "stock_ins_daily": 12, "holder_distribution": 12}),
    (475, {"daily_prices": 30, "stock_ins_daily": 18, "holder_distribution": 18}),
]
ANCHOR_STOCK = "2330"   # 每天都有交易，用它的主鍵索引找過期的日期比較快
MAX_DAYS_PER_RUN = 10   # 每張表每次最多刪這麼多天


def years_ago(day, years):
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 2/29
        return day.replace(year=day.year - years, day=28)


def months_ago(day, months):
    y, m = divmod(day.year * 12 + day.month - 1 - months, 12)
    m += 1
    for d in (day.day, 30, 29, 28):  # 月底沒有那一天就往前找
        try:
            return date(y, m, d)
        except ValueError:
            continue


def retention_months(size_mb):
    """依資料庫用量決定各表保留月數：{資料表: 月數}"""
    months = {table: years * 12 for table, _, years in RETENTION}
    if size_mb is not None:
        for limit, shorter in SPACE_TIERS:
            if size_mb >= limit:
                months.update(shorter)
                break
    return months


def database_mb(sb):
    """資料庫用量（MB）；讀不到回傳 None，照原本期限清除"""
    try:
        return (_execute(lambda: sb.rpc("database_size")).data or 0) / 1024 / 1024
    except Exception as e:
        print(f"  讀不到資料庫用量（{type(e).__name__}），照原本期限清除")
        return None


def cleanup(sb, table, col, years, today, dry_run, archive_only=False, months=None):
    """table 超過 years 年（或 months 個月）的資料先封存、再刪除，一次處理一天。回傳刪除（或封存）筆數"""
    months = months or years * 12
    cutoff = months_ago(today, months).isoformat()
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
    keep = f"{months // 12} 年" if months % 12 == 0 else f"{months} 個月"
    print(f"  {table}：保留 {keep}（{cutoff} 起），{span}，{action} {total:,} 筆")
    return total


def main():
    dry_run = os.getenv("DRY_RUN") == "1"
    archive_only = os.getenv("ARCHIVE_ONLY") == "1"
    override = os.getenv("CLEANUP_DATE", "").strip()
    today = date.fromisoformat(override) if override else datetime.now(timezone.utc).date()
    print(f"========== 清除過期資料：{today}{'（只檢查，不刪除）' if dry_run else '（只封存，不刪除）' if archive_only else ''} ==========")
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
    size_mb = database_mb(sb)
    months = retention_months(size_mb)
    if size_mb is not None:
        tier = next((limit for limit, _ in SPACE_TIERS if size_mb >= limit), None)
        print(f"  資料庫用量 {size_mb:,.0f} MB" + (f"，超過 {tier} MB，自動縮短保留期限" if tier else ""))
        if tier and os.getenv("GITHUB_ACTIONS"):
            print(f"::warning title=資料庫快滿::用量 {size_mb:,.0f} MB，已自動縮短保留期限（先封存再刪除）")
    failed = 0
    for table, col, years in RETENTION:
        try:
            cleanup(sb, table, col, years, today, dry_run, archive_only, months=months[table])
        except Exception as e:
            print(f"❌ {table} 清除失敗：{e}")
            failed += 1
    if failed:
        return 1
    print("✅ 清除完畢")
    return 0


if __name__ == "__main__":
    sys.exit(main())
