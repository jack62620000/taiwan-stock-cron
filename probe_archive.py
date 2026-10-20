"""暫時：在 GitHub Actions 上試跑封存（只封存不刪除），並讀回確認（測完就刪）"""
import os
from supabase import create_client
import archive

sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
bucket = sb.storage.from_(archive.BUCKET)
for table, year, start, end in [("margin_utilization", 2025, "2025-10-01", "2025-12-31"),
                                ("daily_prices", 2024, "2024-01-01", "2024-01-31")]:
    files = bucket.list(f"{table}/{year}", {"limit": 1000})
    sizes = [f.get("metadata", {}).get("size", 0) for f in files]
    print(f"{table}: {len(files)} 個檔案，平均 {sum(sizes) / max(len(sizes), 1) / 1024:.1f} KB")
    df = archive.load_archive(sb, table, start, end)
    print(f"  讀回 {len(df):,} 筆，日期 {df['date'].min()}～{df['date'].max()}，欄位 {list(df.columns)}")
    print(df.head(3).to_string())
    n_db = sb.table(table).select("stock_id", count="exact", head=True).gte("date", start).lte("date", df['date'].max()).execute().count
    print(f"  資料庫同期間 {n_db:,} 筆（只封存沒刪，兩邊應該一樣）")
