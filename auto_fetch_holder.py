"""
週六專用 - 只同步大戶持股比例

先抓集保開放資料（最新一週，一次全部）；開放資料失敗或有缺漏時，改用集保查詢網頁逐檔補最近幾週。
最後檢查最新一週的筆數，明顯不足時以錯誤碼結束，讓 GitHub 把這次排程標成失敗。
"""
import sys
import time
from datetime import date, timedelta

import requests

import fetch_stock_info as f
from fetch_stock_info import get_valid_stock_ids

supabase = f.supabase

OPENDATA_RETRIES = 3
# 開放資料之外，用查詢網頁檢查並補齊最近幾週的缺漏
RECENT_DAYS = 35
FILL_MAX_MINUTES = 60
# 最新一週寫入的檔數低於仍在交易股票數的這個比例，視為失敗
MIN_COVERAGE = 0.95
# 最新一週的資料日距今超過這麼多天，視為這週沒抓到（正常是週五資料、週六抓）
STALE_DAYS = 8


def fetch_holder_distribution(valid_stocks):
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    url = "https://smart.tdcc.com.tw/opendata/getOD.ashx?id=1-5"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    print("正在從集保結算所下載大戶持股資料...")
    try:
        import pandas as pd
        from io import StringIO
        r = None
        for attempt in range(OPENDATA_RETRIES):
            try:
                r = requests.get(url, headers=headers, timeout=60, verify=False)
                r.raise_for_status()
                break
            except Exception as e:
                print(f"  第 {attempt + 1} 次下載失敗：{e}")
                if attempt == OPENDATA_RETRIES - 1:
                    raise
                time.sleep(30 * (attempt + 1))
        r.encoding = "utf-8"
        df = pd.read_csv(StringIO(r.text))

        df = df[df["持股分級"].isin([12, 13, 14, 15])]
        df = df[df["證券代號"].astype(str).str.strip().isin(valid_stocks)]

        def parse_week_date(d):
            d = str(d).strip()
            if len(d) == 8 and d.isdigit():
                return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            return None

        result = (
            df.groupby(["資料日期", "證券代號"])["占集保庫存數比例%"]
            .sum()
            .reset_index()
        )

        records = []
        for _, row in result.iterrows():
            records.append({
                "stock_id": str(row["證券代號"]).strip(),
                "week_date": parse_week_date(str(row["資料日期"]).strip()),
                "big_holder_ratio": round(float(row["占集保庫存數比例%"]), 2),
            })
        return records
    except Exception as e:
        print(f"抓取大戶持股失敗: {e}")
        return []

def fill_recent_weeks(valid_stocks):
    """用集保查詢網頁補最近幾週沒有寫入的股票，回傳 (最新一週, 問題清單)"""
    import fetch_holder_history as history
    today = date.today()
    start = (today - timedelta(days=RECENT_DAYS)).isoformat()
    _, problems, unfinished = history.fetch_range(
        start, today.isoformat(), valid_stocks, deadline=time.time() + FILL_MAX_MINUTES * 60)
    if unfinished:
        problems.append(f"還有 {len(unfinished)} 週沒補完（{unfinished[0]} 起），請執行區間回補 holder")
    return problems


def check_latest_week(valid_stocks):
    """最新一週的筆數是否足夠，回傳問題說明；足夠時回傳 None"""
    import fetch_holder_history as history
    res = supabase.table("holder_distribution").select("week_date").order("week_date", desc=True).limit(1).execute()
    if not res.data:
        return "資料表沒有任何大戶持股資料"
    week = res.data[0]["week_date"]
    delisted = history.delisted_dates()
    active = [sid for sid in valid_stocks if delisted.get(sid, "9999") > week]
    saved = len(history.stocks_already_saved(week))
    print(f"最新一週 {week}：已寫入 {saved} 檔，仍在交易 {len(active)} 檔")
    if (date.today() - date.fromisoformat(week)).days > STALE_DAYS:
        return f"最新一週是 {week}，已超過 {STALE_DAYS} 天沒有新資料"
    if saved < len(active) * MIN_COVERAGE:
        return f"最新一週 {week} 只有 {saved} 檔，少於仍在交易股票的 {MIN_COVERAGE:.0%}"
    return None


if __name__ == "__main__":
    print("========== 週六排程：同步大戶持股比例 ==========")
    valid_stocks = get_valid_stock_ids()
    print(f"資料庫內現有 {len(valid_stocks)} 檔股票可供對應。")
    if not valid_stocks:
        print("❌ 無法取得股票清單，中止執行。")
        sys.exit(1)

    problems = []
    holder_data = fetch_holder_distribution(valid_stocks)
    if not holder_data:
        problems.append("集保開放資料沒有取得資料，改用查詢網頁補抓")
    elif f.save_data_batches("holder_distribution", holder_data)[1]:
        problems.append("開放資料有部分寫入失敗")

    print("\n--- 用集保查詢網頁檢查並補齊最近幾週 ---")
    try:
        fill_problems = fill_recent_weeks(valid_stocks)
    except Exception as e:
        fill_problems = [f"查詢網頁補抓中斷：{type(e).__name__}：{e}"]

    latest_problem = check_latest_week(valid_stocks)
    for p in problems + fill_problems:
        print(f"⚠️ {p}")
    if latest_problem or fill_problems:
        print(f"❌ 大戶持股同步未完成：{latest_problem or '最近幾週仍有缺漏'}")
        sys.exit(1)
    print("\n✅ 大戶持股同步完成！")
