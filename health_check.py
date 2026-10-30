"""
每週系統自我檢查 - 給 GitHub Actions 每週排程使用（.github/workflows/health_check.yml）

做三件事，有問題時以錯誤碼結束，GitHub 會把這次排程標成失敗並寄信：

1. 資料有沒有停更：兩個資料庫每張會持續更新的表，看最新一筆的日期（或月份、季度）離今天多久。
   check_data.py 每天只看股價、法人、融資券、評分四張表的筆數；這裡涵蓋其他來源
   （期貨、大盤、集保、月營收、季報、ETF 各表、指數、美股），某個官方來源改網址或格式、
   抓不到資料時，最晚一週內會發現。容許天數已考慮連假（春節約 9 天）和各資料的公布時間。
2. 每週備份有沒有產生：ETF 專案 Storage 的 db-backups 最新的日期資料夾不能超過 BACKUP_MAX_DAYS 天。
3. 讓排程保持啟用：公開的 repo 60 天沒有任何活動，GitHub 會自動停用所有排程（連資料庫的準時觸發也叫不動）。
   這裡用 GitHub API 把每個 workflow 重新啟用一次，計時就會重算，不需要做假的 commit。

資料庫用量由 check_data.py 每天檢查，超過門檻時 cleanup_old_data.py 會自動縮短保留期限。

用法：python health_check.py（需要 SUPABASE_URL／KEY、ETF_SUPABASE_URL／KEY；
在 GitHub Actions 另外用 GITHUB_TOKEN、GITHUB_REPOSITORY 重新啟用排程）
"""
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from supabase import create_client

from retry import execute as _execute

load_dotenv()

# (資料庫, 資料表, 欄位, 種類, 篩選, 容許天數)
# 種類：day＝日期欄位；month＝'YYYY-MM'，以該月最後一天起算；quarter＝'YYYY-Qn' 或 year/quarter 兩欄，以季底起算。
# 篩選：用權值股或 0050 走主鍵索引，大表也很快
CHECKS = [
    ("main", "daily_prices", "date", "day", ("stock_id", "2330"), 14),
    ("main", "stock_ins_daily", "date", "day", ("stock_id", "2330"), 14),
    ("main", "margin_utilization", "date", "day", ("stock_id", "2330"), 14),
    ("main", "daily_signals", "date", "day", ("stock_id", "2330"), 14),
    ("main", "momentum_signals", "date", "day", None, 14),
    ("main", "market_index", "date", "day", None, 14),
    ("main", "futures_institutional", "date", "day", None, 14),
    ("main", "holder_distribution", "week_date", "day", ("stock_id", "2330"), 21),
    # 月營收每月 10 日前公布、季報最晚季底後約 3 個月（第四季 3/31）
    ("main", "monthly_revenue", "year_month", "month", ("stock_id", "2330"), 55),
    ("main", "quarterly_report", "year,quarter", "quarter", ("stock_id", "2330"), 200),
    ("etf", "etf_daily_prices", "date", "day", ("etf_id", "0050"), 14),
    ("etf", "etf_ins_daily", "date", "day", ("etf_id", "0050"), 14),
    ("etf", "etf_margin_daily", "date", "day", ("etf_id", "0050"), 14),
    ("etf", "etf_sbl_daily", "date", "day", ("etf_id", "0050"), 14),
    ("etf", "etf_nav", "date", "day", ("etf_id", "0050"), 14),
    ("etf", "etf_index_daily", "date", "day", None, 14),
    ("etf", "etf_holder_distribution", "week_date", "day", ("etf_id", "0050"), 21),
    ("etf", "etf_top_holdings", "year_month", "month", ("etf_id", "0050"), 55),
    ("etf", "etf_monthly_stats", "year_month", "month", ("etf_id", "0050"), 55),
    ("etf", "etf_expense_ratio", "period", "quarter", ("etf_id", "0050"), 200),
    ("etf", "us_market_daily", "date", "day", ("symbol", "^GSPC"), 7),
]
BACKUP_BUCKET = "db-backups"
BACKUP_MAX_DAYS = 9   # 每週日備份，晚一兩天補跑也不算異常


def period_end(kind, value):
    """把最新一筆的值換成該期間的最後一天"""
    if kind == "day":
        return date.fromisoformat(str(value)[:10])
    if kind == "month":
        y, m = map(int, str(value).split("-"))
        return date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)
    y, q = value if isinstance(value, tuple) else map(int, re.fullmatch(r"(\d{4})-Q(\d)", value).groups())
    return date(y + q // 4, q * 3 % 12 + 1, 1) - timedelta(days=1)


def latest(sb, table, col, kind, filt):
    """最新一筆的值；季報是 year、quarter 兩欄，取同一年最近幾筆再挑最大的"""
    cols = col.split(",")

    def query():
        q = sb.table(table).select(col)
        if filt:
            q = q.eq(*filt)
        return q.order(cols[0], desc=True).limit(8)
    rows = _execute(query).data
    if not rows:
        return None
    return max(tuple(r[c] for c in cols) for r in rows) if len(cols) > 1 else rows[0][col]


def check_freshness(clients, today):
    """回傳 (說明文字, 問題清單)"""
    lines, problems = [], []
    for db, table, col, kind, filt, max_days in CHECKS:
        try:
            value = latest(clients[db], table, col, kind, filt)
        except Exception as e:
            problems.append(f"{table}：讀取失敗（{type(e).__name__}）")
            continue
        if value is None:
            problems.append(f"{table}：沒有任何資料")
            continue
        age = (today - period_end(kind, value)).days
        shown = "Q".join(map(str, value)) if isinstance(value, tuple) else value
        mark = "✅" if age <= max_days else "❌"
        lines.append(f"{mark} {table:<24} 最新 {shown}（{age} 天前，容許 {max_days} 天）")
        if age > max_days:
            problems.append(f"{table} 最新資料是 {shown}，已經 {age} 天沒有更新（容許 {max_days} 天），"
                            "可能是來源改了格式或抓取失敗，請看對應排程的紀錄")
    return "\n".join(lines), problems


def check_backup(url, key, today):
    """回傳 (說明文字, 問題清單)"""
    try:
        r = requests.post(f"{url.rstrip('/')}/storage/v1/object/list/{BACKUP_BUCKET}",
                          headers={"Authorization": f"Bearer {key}", "apikey": key},
                          json={"prefix": "", "limit": 1000}, timeout=60)
        r.raise_for_status()
    except Exception as e:
        return "", [f"讀不到備份清單（{type(e).__name__}）"]
    days = sorted(o["name"] for o in r.json() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", o["name"]))
    if not days:
        return "", ["Storage 的 db-backups 裡沒有任何備份"]
    age = (today - date.fromisoformat(days[-1])).days
    text = f"最新備份：{days[-1]}（{age} 天前），共 {len(days)} 份"
    if age > BACKUP_MAX_DAYS:
        return text, [f"最新備份是 {days[-1]}，已經 {age} 天沒有新的備份，請看「每週資料庫備份」排程"]
    return text, []


def keep_workflows_enabled(token, repo):
    """把每個 workflow 重新啟用一次，重算 60 天停用的計時。回傳 (說明文字, 問題清單)"""
    api = f"https://api.github.com/repos/{repo}/actions/workflows"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    try:
        r = requests.get(api, headers=headers, params={"per_page": 100}, timeout=30)
        r.raise_for_status()
        workflows = r.json()["workflows"]
    except Exception as e:
        return "", [f"讀不到 workflow 清單（{type(e).__name__}）"]
    problems, done = [], 0
    for w in workflows:
        res = requests.put(f"{api}/{w['id']}/enable", headers=headers, timeout=30)
        if res.status_code == 204:
            done += 1
        else:
            problems.append(f"排程「{w['name']}」重新啟用失敗（HTTP {res.status_code}）")
    return f"重新啟用排程：{done} / {len(workflows)} 個", problems


def main():
    today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
    print(f"========== 每週系統自我檢查：{today} ==========")
    etf_url, etf_key = os.getenv("ETF_SUPABASE_URL"), os.getenv("ETF_SUPABASE_KEY")
    clients = {"main": create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")),
               "etf": create_client(etf_url, etf_key)}
    sections = [("資料更新", check_freshness(clients, today)), ("每週備份", check_backup(etf_url, etf_key, today))]
    token, repo = os.getenv("GITHUB_TOKEN"), os.getenv("GITHUB_REPOSITORY")
    if token and repo:
        sections.append(("排程啟用", keep_workflows_enabled(token, repo)))
    problems = []
    for title, (text, probs) in sections:
        print(f"\n【{title}】\n{text}")
        problems += probs
    if problems:
        print("\n❌ 發現問題：")
        for p in problems:
            print(f"   - {p}")
            if os.getenv("GITHUB_ACTIONS"):
                print(f"::error title=系統自我檢查::{p}")
        return 1
    print("\n✅ 資料都有持續更新，備份正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
