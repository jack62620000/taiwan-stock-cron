"""
每週資料庫備份 - 把主資料庫和 ETF 資料庫整個匯出，存到 ETF 資料庫 Storage 的 db-backups 桶，只留最近 KEEP 份

Supabase 免費方案沒有資料庫備份；archive.py 封存的只是過期被清掉的舊資料，
這裡備份的是資料庫裡現在全部的資料（含表格結構），用在誤刪、改表改壞或資料庫壞掉時還原。

做法：pg_dump 壓縮格式（-Fc）匯出 public（主資料庫另含準時觸發用的 ops）schema，
用 pg_restore --list 確認檔案完整，再切成每塊 PART_SIZE 上傳（免費方案 Storage 單檔上限 50 MB）。
放在 db-backups/<日期>/<資料庫>.dump.000、.001…，新的一份全部上傳成功才刪掉更舊的。

需要的 secrets：MAIN_DB_URL、ETF_DB_URL（Supabase 專案 Connect → Session pooler 的連線字串，含密碼），
ETF_SUPABASE_URL、ETF_SUPABASE_KEY（上傳 Storage 用）。

還原：把同一份的分塊依序接起來再 pg_restore，例如
  cat main.dump.* > main.dump
  pg_restore --clean --if-exists --no-owner --no-privileges -d "<連線字串>" main.dump
"""
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import requests

BUCKET = "db-backups"
KEEP = 2
PART_SIZE = 45 * 1024 * 1024
TIMEOUT = 300

# (名稱, 連線字串的環境變數, 要匯出的 schema)
DATABASES = [
    ("main", "MAIN_DB_URL", ["public", "ops"]),
    ("etf", "ETF_DB_URL", ["public"]),
]


def old_backups(folders, keep=KEEP):
    """日期資料夾中，超過最近 keep 份的（要刪掉的）"""
    return sorted(folders, reverse=True)[keep:]


def split_parts(data, size=PART_SIZE):
    return [data[i:i + size] for i in range(0, len(data), size)] or [b""]


class Storage:
    def __init__(self, url, key):
        self.base = url.rstrip("/") + "/storage/v1"
        self.headers = {"Authorization": f"Bearer {key}", "apikey": key}

    def _call(self, method, path, **kw):
        r = requests.request(method, self.base + path, headers={**self.headers, **kw.pop("headers", {})},
                             timeout=TIMEOUT, **kw)
        return r

    def ensure_bucket(self):
        r = self._call("POST", "/bucket", json={"id": BUCKET, "name": BUCKET, "public": False})
        if r.status_code not in (200, 201) and "already exists" not in r.text.lower():
            r.raise_for_status()

    def upload(self, path, data):
        r = self._call("POST", f"/object/{BUCKET}/{path}", data=data,
                       headers={"Content-Type": "application/octet-stream", "x-upsert": "true"})
        r.raise_for_status()

    def list(self, prefix=""):
        r = self._call("POST", f"/object/list/{BUCKET}", json={"prefix": prefix, "limit": 1000})
        r.raise_for_status()
        return r.json()

    def remove(self, paths):
        if paths:
            self._call("DELETE", f"/object/{BUCKET}", json={"prefixes": paths}).raise_for_status()


def dump(name, conn, schemas, out):
    args = ["pg_dump", "-Fc", "--no-owner", "--no-privileges", "-f", out]
    for s in schemas:
        args += ["-n", s]
    subprocess.run(args + ["--dbname", conn], check=True)
    subprocess.run(["pg_restore", "--list", out], check=True, stdout=subprocess.DEVNULL)
    return out


def main():
    storage = Storage(os.environ["ETF_SUPABASE_URL"], os.environ["ETF_SUPABASE_KEY"])
    storage.ensure_bucket()
    day = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")
    # 同一天重跑時先清掉那天的舊分塊，避免新檔較小時留下多的分塊
    storage.remove([f"{day}/{o['name']}" for o in storage.list(day + "/")])

    for name, env, schemas in DATABASES:
        conn = os.environ.get(env)
        if not conn:
            sys.exit(f"沒有設定 {env}，無法備份 {name} 資料庫")
        out = dump(name, conn, schemas, f"{name}.dump")
        with open(out, "rb") as f:
            data = f.read()
        parts = split_parts(data)
        for i, part in enumerate(parts):
            storage.upload(f"{day}/{name}.dump.{i:03d}", part)
        print(f"{name}：{len(data) / 1024 / 1024:.1f} MB，分 {len(parts)} 塊上傳到 {BUCKET}/{day}/")

    # 這份全部上傳成功才清舊的
    folders = [o["name"] for o in storage.list() if o.get("id") is None]
    for folder in old_backups(folders):
        files = [f"{folder}/{o['name']}" for o in storage.list(folder + "/")]
        storage.remove(files)
        print(f"刪除舊備份 {folder}（{len(files)} 個檔案）")


if __name__ == "__main__":
    main()
