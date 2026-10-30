"""
每週資料庫備份 - 把主資料庫和 ETF 資料庫整個匯出，存到 ETF 資料庫 Storage 的 db-backups 桶，只留最近 KEEP 份

Supabase 免費方案沒有資料庫備份；archive.py 封存的只是過期被清掉的舊資料，
這裡備份的是資料庫裡現在全部的資料（含表格結構），用在誤刪、改表改壞或資料庫壞掉時還原。

做法：pg_dump 壓縮格式（-Fc）匯出 public（主資料庫另含準時觸發用的 ops）schema，
用 pg_restore --list 確認檔案完整，再切成每塊 PART_SIZE 上傳（免費方案 Storage 單檔上限 50 MB）。
放在 db-backups/<日期>/<資料庫>.dump.000、.001…，新的一份全部上傳成功才刪掉更舊的。

過期資料的封存檔（兩個專案 Storage 的 archive 桶，見 archive.py）也一起備份，而且互相放到另一個專案：
主資料庫的封存檔 → ETF 專案 db-backups/archive/，ETF 的封存檔 → 主專案 db-backups/archive/，
路徑和原本一樣。封存檔寫好就不會變，每次只複製新增的檔案；LINE 查詢卡片每天重新產生，不備份。

另外上傳一份到 Google 雲端硬碟「台股資料庫備份/<日期>/」（main.dump、etf.dump 不分塊，
兩邊封存檔打包成 archive.tar），一樣只留最近 KEEP 份。需要 GDRIVE_CLIENT_ID、GDRIVE_CLIENT_SECRET、
GDRIVE_REFRESH_TOKEN（Google Cloud 的 OAuth 用戶端，權限 drive.file），沒設定就略過這一步。

需要的 secrets：MAIN_DB_PASSWORD、ETF_DB_PASSWORD（兩個資料庫的密碼），SUPABASE_URL、ETF_SUPABASE_URL
（從網址取得專案代號），SUPABASE_KEY、ETF_SUPABASE_KEY（讀寫 Storage 用）。
GitHub Actions 只能用 IPv4，資料庫直連是 IPv6，所以經過 Supabase 的 Session pooler 連線；
專案在哪一組 pooler 主機無法事先得知，依序試 POOLER_HOSTS。

還原：把同一份的分塊依序接起來再 pg_restore，例如
  cat main.dump.* > main.dump   （雲端硬碟上的 main.dump 沒有分塊，直接用）
  pg_restore --clean --if-exists --no-owner --no-privileges -d "<連線字串>" main.dump
"""
import io
import json
import os
import re
import subprocess
import sys
import tarfile
from datetime import datetime, timedelta, timezone

from urllib.parse import quote

import requests

BUCKET = "db-backups"
KEEP = 2
PART_SIZE = 45 * 1024 * 1024
TIMEOUT = 300
ARCHIVE_BUCKET = "archive"
ARCHIVE_PREFIX = "archive/"
# LINE 查詢卡片每天重新產生，不用備份
ARCHIVE_SKIP = ("_line_cards/",)
DRIVE_FOLDER = "台股資料庫備份"

# 兩個專案都在首爾（ap-northeast-2）
POOLER_HOSTS = ["aws-0-ap-northeast-2.pooler.supabase.com", "aws-1-ap-northeast-2.pooler.supabase.com"]

# (名稱, 專案網址的環境變數, 密碼的環境變數, 要匯出的 schema)
DATABASES = [
    ("main", "SUPABASE_URL", "MAIN_DB_PASSWORD", ["public", "ops"]),
    ("etf", "ETF_SUPABASE_URL", "ETF_DB_PASSWORD", ["public"]),
]


def old_backups(folders, keep=KEEP):
    """日期資料夾中，超過最近 keep 份的（要刪掉的）；archive 等其他資料夾不動"""
    days = [f for f in folders if re.fullmatch(r"\d{4}-\d{2}-\d{2}", f)]
    return sorted(days, reverse=True)[keep:]


def pooler_urls(project_url, password):
    """Session pooler 連線字串（每組主機一個），密碼的特殊字元要編碼"""
    ref = re.match(r"https://([a-z0-9]+)\.supabase\.co", project_url.strip()).group(1)
    return [f"postgresql://postgres.{ref}:{quote(password, safe='')}@{host}:5432/postgres"
            for host in POOLER_HOSTS]


def split_parts(data, size=PART_SIZE):
    return [data[i:i + size] for i in range(0, len(data), size)] or [b""]


class Storage:
    def __init__(self, url, key, bucket=BUCKET):
        self.base = url.rstrip("/") + "/storage/v1"
        self.headers = {"Authorization": f"Bearer {key}", "apikey": key}
        self.bucket = bucket

    def _call(self, method, path, **kw):
        r = requests.request(method, self.base + path, headers={**self.headers, **kw.pop("headers", {})},
                             timeout=TIMEOUT, **kw)
        return r

    def ensure_bucket(self):
        r = self._call("POST", "/bucket", json={"id": self.bucket, "name": self.bucket, "public": False})
        if r.status_code not in (200, 201) and "already exists" not in r.text.lower():
            r.raise_for_status()

    def upload(self, path, data):
        r = self._call("POST", f"/object/{self.bucket}/{path}", data=data,
                       headers={"Content-Type": "application/octet-stream", "x-upsert": "true"})
        r.raise_for_status()

    def download(self, path):
        r = self._call("GET", f"/object/{self.bucket}/{path}")
        r.raise_for_status()
        return r.content

    def list(self, prefix=""):
        r = self._call("POST", f"/object/list/{self.bucket}", json={"prefix": prefix, "limit": 1000})
        r.raise_for_status()
        return r.json()

    def walk(self, prefix=""):
        """prefix 底下所有檔案（含子資料夾）：{路徑: 大小}"""
        files = {}
        for o in self.list(prefix):
            path = prefix + o["name"]
            if o.get("id") is None:
                files.update(self.walk(path + "/"))
            else:
                files[path] = (o.get("metadata") or {}).get("size")
        return files

    def remove(self, paths):
        if paths:
            self._call("DELETE", f"/object/{self.bucket}", json={"prefixes": paths}).raise_for_status()


def files_to_copy(src, dst, skip=()):
    """來源有、但備份沒有或大小不同的檔案（封存檔寫好就不會再變，平常每週只會多幾個新檔）"""
    return sorted(p for p, size in src.items()
                  if not p.startswith(tuple(skip)) and dst.get(p) != size)


def mirror_archive(name, src, dst, prefix):
    """把 src 的封存檔（archive 桶）複製到 dst 備份桶的 prefix/ 底下"""
    dst_files = {p[len(prefix):]: size for p, size in dst.walk(prefix).items()}
    todo = files_to_copy(src.walk(), dst_files, ARCHIVE_SKIP)
    for path in todo:
        dst.upload(prefix + path, src.download(path))
    print(f"{name} 封存檔：新增或更新 {len(todo)} 個，備份在 {dst.bucket}/{prefix}")


class Drive:
    """Google 雲端硬碟：用 OAuth refresh token 換存取權杖，權限只到這個程式自己建立的檔案（drive.file）"""
    API = "https://www.googleapis.com/drive/v3/files"
    FOLDER = "application/vnd.google-apps.folder"

    def __init__(self, client_id, client_secret, refresh_token):
        r = requests.post("https://oauth2.googleapis.com/token", timeout=TIMEOUT, data={
            "client_id": client_id, "client_secret": client_secret,
            "refresh_token": refresh_token, "grant_type": "refresh_token"})
        r.raise_for_status()
        self.headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    def children(self, parent=None, folders_only=False):
        q = ["trashed=false", f"'{parent}' in parents" if parent else "'root' in parents"]
        if folders_only:
            q.append(f"mimeType='{self.FOLDER}'")
        r = requests.get(self.API, headers=self.headers, timeout=TIMEOUT,
                         params={"q": " and ".join(q), "fields": "files(id,name)", "pageSize": 1000})
        r.raise_for_status()
        return {f["name"]: f["id"] for f in r.json()["files"]}

    def folder(self, name, parent=None):
        """找不到就建立"""
        found = self.children(parent, folders_only=True).get(name)
        if found:
            return found
        meta = {"name": name, "mimeType": self.FOLDER, **({"parents": [parent]} if parent else {})}
        r = requests.post(self.API, headers=self.headers, json=meta, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()["id"]

    def upload(self, name, parent, data):
        """可續傳上傳，大檔案也一次傳完"""
        r = requests.post("https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable",
                          headers={**self.headers, "Content-Type": "application/json; charset=UTF-8"},
                          data=json.dumps({"name": name, "parents": [parent]}), timeout=TIMEOUT)
        r.raise_for_status()
        requests.put(r.headers["Location"], data=data, timeout=TIMEOUT * 4).raise_for_status()

    def delete(self, file_id):
        requests.delete(f"{self.API}/{file_id}", headers=self.headers, timeout=TIMEOUT).raise_for_status()


def archive_tar(sources):
    """兩邊封存檔打包成一個 tar（檔案本身已是 gz，不再壓縮），路徑前面加上專案名稱"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, src in sources:
            for path in sorted(src.walk()):
                if path.startswith(ARCHIVE_SKIP):
                    continue
                data = src.download(path)
                info = tarfile.TarInfo(f"{name}/{path}")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def backup_to_drive(day, dumps, archive_sources):
    """備份也上傳到 Google 雲端硬碟「台股資料庫備份/<日期>/」，只留最近 KEEP 份；沒設定 secrets 就略過"""
    keys = [os.environ.get(k) for k in ("GDRIVE_CLIENT_ID", "GDRIVE_CLIENT_SECRET", "GDRIVE_REFRESH_TOKEN")]
    if not all(keys):
        print("沒有設定 Google 雲端硬碟的 secrets，略過")
        return
    drive = Drive(*keys)
    root = drive.folder(DRIVE_FOLDER)
    old = drive.children(root, folders_only=True).get(day)
    if old:  # 同一天重跑：整個資料夾換新
        drive.delete(old)
    target = drive.folder(day, root)
    for name in dumps:
        with open(f"{name}.dump", "rb") as f:
            drive.upload(f"{name}.dump", target, f.read())
    drive.upload("archive.tar", target, archive_tar(archive_sources))
    print(f"Google 雲端硬碟：已上傳到 {DRIVE_FOLDER}/{day}/")
    folders = drive.children(root, folders_only=True)
    for folder in old_backups(folders):
        drive.delete(folders[folder])
        print(f"Google 雲端硬碟：刪除舊備份 {folder}")


def dump(name, urls, schemas, out):
    args = ["pg_dump", "-Fc", "--no-owner", "--no-privileges", "-f", out]
    for s in schemas:
        args += ["-n", s]
    for i, url in enumerate(urls):
        r = subprocess.run(args + ["--dbname", url], capture_output=True, text=True)
        if r.returncode == 0:
            break
        # 錯誤訊息不含密碼；專案不在這組主機時會是 Tenant or user not found，換下一組
        print(f"{name}：第 {i + 1} 組 pooler 主機失敗：{r.stderr.strip()[:300]}")
    else:
        sys.exit(f"{name} 資料庫匯出失敗（密碼錯誤或連不上）")
    subprocess.run(["pg_restore", "--list", out], check=True, stdout=subprocess.DEVNULL)
    return out


def main():
    storage = Storage(os.environ["ETF_SUPABASE_URL"], os.environ["ETF_SUPABASE_KEY"])
    storage.ensure_bucket()
    day = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")
    # 同一天重跑時先清掉那天的舊分塊，避免新檔較小時留下多的分塊
    storage.remove([f"{day}/{o['name']}" for o in storage.list(day + "/")])

    for name, url_env, password_env, schemas in DATABASES:
        password = os.environ.get(password_env)
        if not password:
            sys.exit(f"沒有設定 {password_env}，無法備份 {name} 資料庫")
        out = dump(name, pooler_urls(os.environ[url_env], password), schemas, f"{name}.dump")
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

    # 過期資料的封存檔：互相備份到另一個專案的 Storage，一個專案出事時另一邊還有一份。
    # 封存檔不會變動，只複製新增的檔案，不輪替也不刪除
    main_url, main_key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"]
    main_backup = Storage(main_url, main_key)
    main_backup.ensure_bucket()
    mirror_archive("主資料庫", Storage(main_url, main_key, ARCHIVE_BUCKET), storage, ARCHIVE_PREFIX)
    etf_archive = Storage(os.environ["ETF_SUPABASE_URL"], os.environ["ETF_SUPABASE_KEY"], ARCHIVE_BUCKET)
    main_archive = Storage(main_url, main_key, ARCHIVE_BUCKET)
    mirror_archive("主資料庫", main_archive, storage, ARCHIVE_PREFIX)
    mirror_archive("ETF 資料庫", etf_archive, main_backup, ARCHIVE_PREFIX)

    # 再放一份到 Supabase 以外（Google 雲端硬碟），Supabase 帳號出問題時也還有備份
    backup_to_drive(day, [name for name, *_ in DATABASES], [("main", main_archive), ("etf", etf_archive)])


if __name__ == "__main__":
    main()
