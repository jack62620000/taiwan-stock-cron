"""
每月備份還原測試 - 給 GitHub Actions 每月排程使用（.github/workflows/restore_test.yml）

db_backup.py 每週只用 pg_restore --list 確認備份檔完整；這裡真的把最新一份備份還原到 GitHub 上的臨時資料庫
（workflow 的 PostgreSQL 17 服務容器，跑完就消失），再逐表比對還原後的筆數和現在的資料庫，
確定萬一要還原時備份真的能用。

比對：每張表還原後的筆數要在現在筆數的 MIN_RATIO～MAX_RATIO 之間（備份是幾天前的，
這幾天有新增、也有清掉過期的，不會完全一樣）；現在有資料、還原後卻是 0 筆就一定算失敗。
pg_restore 的錯誤（例如 Supabase 內建的角色、擴充功能在臨時資料庫沒有）只列出來，不算失敗，
重點是資料表和資料都有還原回來。

需要：SUPABASE_URL、ETF_SUPABASE_URL（專案代號）、ETF_SUPABASE_KEY（讀備份）、MAIN_DB_PASSWORD、ETF_DB_PASSWORD
（讀現在的筆數），RESTORE_URL（臨時資料庫，例如 postgresql://postgres:postgres@localhost:5432）。
"""
import os
import re
import subprocess
import sys

from db_backup import BUCKET, DATABASES, Storage, pooler_urls

MIN_RATIO, MAX_RATIO = 0.9, 1.1


def psql(url, sql):
    r = subprocess.run(["psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-d", url, "-c", sql],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:300])
    return [line.split("|") for line in r.stdout.strip().splitlines() if line]


def live_url(urls):
    """依序試每組 pooler 主機，回傳第一個連得上的"""
    for url in urls:
        try:
            psql(url, "select 1")
            return url
        except RuntimeError as e:
            print(f"  pooler 主機連不上：{e}")
    raise RuntimeError("連不上現在的資料庫（密碼錯誤或連不上）")


def compare(restored, live):
    """restored、live：{表: 筆數}。回傳問題清單"""
    problems = []
    for table, n_live in sorted(live.items()):
        n = restored.get(table)
        if n is None:
            problems.append(f"{table}：備份裡沒有這張表")
        elif n_live and not n:
            problems.append(f"{table}：現在有 {n_live:,} 筆，還原後是 0 筆")
        elif n_live and not MIN_RATIO <= n / n_live <= MAX_RATIO:
            problems.append(f"{table}：還原 {n:,} 筆，現在 {n_live:,} 筆，差太多")
    return problems


def counts(url, schemas):
    tables = psql(url, "select schemaname || '.' || relname from pg_stat_user_tables "
                       f"where schemaname in ({', '.join(repr(s) for s in schemas)}) order by 1")
    return {t: int(psql(url, f"select count(*) from {t}")[0][0]) for (t,) in tables}


def main():
    storage = Storage(os.environ["ETF_SUPABASE_URL"], os.environ["ETF_SUPABASE_KEY"])
    days = sorted(o["name"] for o in storage.list() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", o["name"]))
    if not days:
        sys.exit("Storage 的 db-backups 裡沒有任何備份")
    day = days[-1]
    print(f"還原測試：{BUCKET}/{day}/")
    base = os.environ["RESTORE_URL"].rstrip("/")
    problems = []
    for name, url_env, password_env, schemas in DATABASES:
        parts = sorted(o["name"] for o in storage.list(day + "/") if o["name"].startswith(f"{name}.dump."))
        if not parts:
            problems.append(f"{name}：{day} 沒有備份檔")
            continue
        with open(f"{name}.dump", "wb") as fh:
            for p in parts:
                fh.write(storage.download(f"{day}/{p}"))
        print(f"\n【{name}】{len(parts)} 塊，{os.path.getsize(f'{name}.dump') / 1024 / 1024:.1f} MB")
        psql(f"{base}/postgres", f"create database restore_{name}")
        target = f"{base}/restore_{name}"
        for s in schemas:
            if s != "public":
                psql(target, f"create schema if not exists {s}")
        r = subprocess.run(["pg_restore", "--no-owner", "--no-privileges", "-d", target, f"{name}.dump"],
                           capture_output=True, text=True)
        errors = [line for line in r.stderr.splitlines() if "error:" in line]
        print(f"pg_restore 錯誤 {len(errors)} 個（Supabase 內建角色、擴充功能不在臨時資料庫，可忽略）")
        for line in errors[:10]:
            print(f"  {line[:200]}")
        restored = counts(target, schemas)
        live = counts(live_url(pooler_urls(os.environ[url_env], os.environ[password_env])), schemas)
        for t in sorted(live):
            print(f"  {t:<36} 還原 {restored.get(t, 0):>10,}　現在 {live[t]:>10,}")
        problems += [f"{name} {p}" for p in compare(restored, live)]
    if problems:
        print("\n❌ 還原測試有問題：")
        for p in problems:
            print(f"   - {p}")
            if os.getenv("GITHUB_ACTIONS"):
                print(f"::error title=備份還原測試::{p}")
        return 1
    print(f"\n✅ {day} 的備份可以完整還原，各表筆數和現在一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
