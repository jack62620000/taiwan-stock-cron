"""
自動化版本 - 給 GitHub Actions 排程使用
每次執行會同步一個交易日的股價、三大法人、融資融券（餘額＋限額＋使用率）。

抓取函式全部來自 fetch_stock_info.py，這裡不再複製一份，
之後只要改 fetch_stock_info.py，排程就會跟著更新。

有資料缺漏或寫入失敗時會以錯誤碼結束，GitHub 會把這次執行標成失敗並寄信通知。
"""
import os
import sys
import time
from datetime import datetime, timezone

import fetch_stock_info as f

SLEEP_BETWEEN_API = 2


def target_date():
    """
    決定要抓哪一天。
    - 設了環境變數 TARGET_DATE（YYYY-MM-DD）就抓那一天，用來手動補抓。
    - 否則用 UTC 日期。排程在 UTC 15:11（台灣 23:11）執行，就算 GitHub 延遲到
      台灣時間過了午夜，UTC 日期仍然是當天的交易日；改用台灣時間反而會抓錯天。
    """
    override = os.getenv("TARGET_DATE", "").strip()
    if override:
        datetime.strptime(override, "%Y-%m-%d")  # 格式錯誤就直接報錯
        return override
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def main():
    today = target_date()
    print(f"========== 自動排程執行：{today} ==========")

    if datetime.strptime(today, "%Y-%m-%d").weekday() >= 5:
        print(f"⚠️ {today} 為週末，台股無交易，本次排程不執行任何抓取作業。")
        return 0

    print("\n--- 更新個股基本資訊 ---")
    f.refresh_stock_info()
    valid_stocks = f.get_valid_stock_ids()
    if not valid_stocks:
        print("❌ 無法取得股票清單，中止執行。")
        return 1
    print(f"資料庫內現有 {len(valid_stocks)} 檔股票可供對應。")

    print("\n--- 下載每日股價 ---")
    twse_prices = f.fetch_twse_daily_all(today, valid_stocks)
    time.sleep(SLEEP_BETWEEN_API)
    tpex_prices = f.fetch_tpex_otc_daily(today, valid_stocks)
    time.sleep(SLEEP_BETWEEN_API)

    # 上市、上櫃都沒有行情才視為休市（國定假日、颱風假）
    if not twse_prices and not tpex_prices:
        print(f"\nℹ️ {today} 上市、上櫃都沒有行情，視為休市日，不寫入任何資料。")
        return 0

    print("\n--- 下載三大法人 ---")
    twse_ins = f.fetch_twse_stock_ins(today, valid_stocks)
    time.sleep(SLEEP_BETWEEN_API)
    tpex_ins = f.fetch_tpex_stock_ins(today, valid_stocks)
    time.sleep(SLEEP_BETWEEN_API)

    print("\n--- 下載融資融券 ---")
    twse_margin = f.fetch_twse_margin(today, valid_stocks)
    time.sleep(SLEEP_BETWEEN_API)
    tpex_margin = f.fetch_tpex_margin(today, valid_stocks)

    fetched = {
        "上市股價": twse_prices, "上櫃股價": tpex_prices,
        "上市法人": twse_ins,    "上櫃法人": tpex_ins,
        "上市融資券": twse_margin, "上櫃融資券": tpex_margin,
    }
    problems = [f"{name}沒有抓到資料" for name, records in fetched.items() if not records]

    print("\n--- 寫入資料庫 ---")
    all_margin = twse_margin + tpex_margin
    for table, records in [
        ("daily_prices",       twse_prices + tpex_prices),
        ("stock_ins_daily",    twse_ins + tpex_ins),
        ("margin_utilization", all_margin),
    ]:
        _, failed = f.save_data_batches(table, records)
        if failed:
            problems.append(f"{table} 有 {failed} 筆寫入失敗")

    if problems:
        print(f"\n❌ {today} 的同步不完整：")
        for p in problems:
            print(f"   - {p}")
        print("   修正後可到 Actions 手動執行，並在 target_date 填入這個日期補抓。")
        return 1

    print("\n✅ 自動排程執行完畢！")
    return 0


if __name__ == "__main__":
    sys.exit(main())
