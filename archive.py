"""
舊資料封存 - 清除過期資料前，先把那一天的資料存成壓縮檔（csv.gz）放到 Supabase Storage

為什麼：Supabase 免費方案資料庫只有 500 MB，但另有 1 GB 檔案空間（Storage）分開計算。
壓縮後的檔案比資料庫小很多（股價一天約 2 千檔、壓縮後約二三十 KB），
舊資料搬到檔案空間，資料庫維持固定大小，資料也不會丟。

檔案位置：archive 桶（不公開）/ 資料表 / 年 / 日期.csv.gz，例如 daily_prices/2023/2023-10-20.csv.gz

讀取封存資料（回測、研究用）：
    from archive import load_archive
    df = load_archive(sb, "daily_prices", "2023-01-01", "2023-12-31")
或在命令列匯出成 CSV：
    python archive.py daily_prices 2023-01-01 2023-12-31 prices_2023.csv
"""
import gzip
import io
import os
import sys

import pandas as pd
from dotenv import load_dotenv
from supabase import create_client

BUCKET = "archive"
PAGE_SIZE = 1000


def archive_path(table, day):
    return f"{table}/{day[:4]}/{day}.csv.gz"


def _fetch_day(sb, table, col, day):
    rows, start = [], 0
    while True:
        res = sb.table(table).select("*").eq(col, day).order("stock_id", desc=False) \
            .range(start, start + PAGE_SIZE - 1).execute()
        rows.extend(res.data or [])
        if len(res.data or []) < PAGE_SIZE:
            return rows
        start += PAGE_SIZE


def archive_day(sb, table, col, day):
    """
    把 table 在 day 的全部資料存成 csv.gz 上傳，再下載回來確認筆數一致。回傳筆數。
    任何一步失敗都會丟出錯誤，呼叫端就不會刪除資料。
    """
    rows = _fetch_day(sb, table, col, day)
    if not rows:
        return 0
    data = gzip.compress(pd.DataFrame(rows).to_csv(index=False).encode("utf-8"))
    path = archive_path(table, day)
    bucket = sb.storage.from_(BUCKET)
    bucket.upload(path, data, {"content-type": "application/gzip", "upsert": "true"})
    saved = pd.read_csv(io.BytesIO(bucket.download(path)), compression="gzip", dtype=str)
    if len(saved) != len(rows):
        raise RuntimeError(f"{path} 封存後只讀回 {len(saved)} 筆，應有 {len(rows)} 筆")
    return len(rows)


def load_archive(sb, table, start, end):
    """讀取 table 在 start～end（YYYY-MM-DD，含頭尾）之間的封存資料"""
    bucket = sb.storage.from_(BUCKET)
    frames = []
    for year in range(int(start[:4]), int(end[:4]) + 1):
        files = bucket.list(f"{table}/{year}", {"limit": 1000, "sortBy": {"column": "name", "order": "asc"}})
        for f in files:
            day = f["name"].removesuffix(".csv.gz")
            if start <= day <= end:
                raw = bucket.download(archive_path(table, day))
                frames.append(pd.read_csv(io.BytesIO(raw), compression="gzip", dtype={"stock_id": str}))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main(argv):
    if len(argv) != 5:
        print("用法：python archive.py 資料表 開始日 結束日 輸出.csv")
        return 1
    _, table, start, end, out = argv
    load_dotenv()
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
    df = load_archive(sb, table, start, end)
    df.to_csv(out, index=False)
    print(f"{table} {start}～{end}：{len(df):,} 筆，已存到 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
