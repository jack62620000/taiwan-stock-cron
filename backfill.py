"""
區間回補 - 給 GitHub Actions 手動執行（本機也能用環境變數執行）

抓取函式全部來自 fetch_stock_info.py，請求節奏與互動版的區間模式相同。

環境變數：
  START_DATE  開始日期 YYYY-MM-DD（必填）
  END_DATE    結束日期 YYYY-MM-DD（留空 = 今天）
  DATASETS    要回補的資料，逗號分隔：
                prices = 每日股價
                ins    = 三大法人
                margin = 融資融券（餘額＋限額＋使用率）

有缺漏、寫入失敗或疑似被來源封鎖時，會以錯誤碼結束，GitHub 會把這次執行標成失敗。
"""
import os
import sys
import time
from datetime import datetime, timezone

import fetch_stock_info as f

SLEEP_BETWEEN_API    = 2
SLEEP_BETWEEN_DAYS   = 5
SLEEP_BATCH_EVERY    = 15
SLEEP_BATCH_DURATION = 30

# 上市連續這麼多天抓不到、但上櫃有資料 → 疑似被證交所封鎖，提早停止
MAX_TWSE_ONLY_FAILS = 3
# 連續這麼多個平日完全沒資料 → 疑似網路或來源異常，提早停止
MAX_EMPTY_DAYS = 8

# 名稱, 上市抓取, 上櫃抓取, 要寫入的 (資料表, 資料)
DATASETS = {
    "prices": (
        "每日股價",
        lambda d, v: f.fetch_twse_daily_all(d, v),
        lambda d, v: f.fetch_tpex_otc_daily(d, v),
        lambda records: [("daily_prices", records)],
    ),
    "ins": (
        "三大法人",
        lambda d, v: f.fetch_twse_stock_ins(d, v),
        lambda d, v: f.fetch_tpex_stock_ins(d, v),
        lambda records: [("stock_ins_daily", records)],
    ),
    "margin": (
        "融資融券",
        lambda d, v: f.fetch_twse_margin(d, v),
        lambda d, v: f.fetch_tpex_margin(d, v),
        lambda records: [
            ("margin_utilization", records),
            ("margin_trading", f.to_margin_trading(records)),
        ],
    ),
}


def read_config():
    start = os.getenv("START_DATE", "").strip()
    end = os.getenv("END_DATE", "").strip() or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    keys = [k.strip() for k in os.getenv("DATASETS", "").split(",") if k.strip()]

    errors = []
    for label, value in (("START_DATE", start), ("END_DATE", end)):
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            errors.append(f"{label} 格式錯誤（{value!r}），請使用 YYYY-MM-DD")
    if not errors and start > end:
        errors.append("開始日期不能晚於結束日期")
    if not keys:
        errors.append("DATASETS 沒有指定要回補的資料")
    unknown = [k for k in keys if k not in DATASETS]
    if unknown:
        errors.append(f"DATASETS 有無法辨識的項目：{', '.join(unknown)}（可用：{', '.join(DATASETS)}）")
    return start, end, keys, errors


def write_summary(lines):
    """在 GitHub Actions 的執行摘要頁顯示結果；本機執行時略過。"""
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


def main():
    start, end, keys, errors = read_config()
    if errors:
        for e in errors:
            print(f"❌ {e}")
        return 1

    names = "、".join(DATASETS[k][0] for k in keys)
    print(f"========== 區間回補：{start} ～ {end}｜{names} ==========")

    days = f.get_trading_days(start, end)
    if not days:
        print("指定區間內沒有交易日。")
        return 0
    print(f"共 {len(days)} 個平日（已排除今年公告的休市日）：{days[0]} ～ {days[-1]}")

    print("\n--- 更新個股基本資訊 ---")
    f.refresh_stock_info()
    valid_stocks = f.get_valid_stock_ids()
    if not valid_stocks:
        print("❌ 無法取得股票清單，中止執行。")
        return 1
    print(f"資料庫內現有 {len(valid_stocks)} 檔股票可供對應。")

    filled_days, empty_days, problems = [], [], []
    twse_only_fails = consecutive_empty = 0
    aborted = None

    for i, day in enumerate(days, 1):
        print(f"\n========== [{i}/{len(days)}] {day} ==========")
        day_has_data = False
        twse_missing = False

        for key in keys:
            label, fetch_twse, fetch_tpex, to_tables = DATASETS[key]
            twse = fetch_twse(day, valid_stocks)
            time.sleep(SLEEP_BETWEEN_API)
            tpex = fetch_tpex(day, valid_stocks)
            time.sleep(SLEEP_BETWEEN_API)

            if not twse and not tpex:
                print(f"{label}：上市、上櫃都沒有資料")
                continue

            day_has_data = True
            if not twse:
                twse_missing = True
                problems.append(f"{day} {label}：缺上市")
            if not tpex:
                problems.append(f"{day} {label}：缺上櫃")

            for table, records in to_tables(twse + tpex):
                _, failed = f.save_data_batches(table, records)
                if failed:
                    problems.append(f"{day} {table}：{failed} 筆寫入失敗")

        if day_has_data:
            filled_days.append(day)
            consecutive_empty = 0
        else:
            empty_days.append(day)
            consecutive_empty += 1
        twse_only_fails = twse_only_fails + 1 if twse_missing else 0

        if twse_only_fails >= MAX_TWSE_ONLY_FAILS:
            aborted = f"上市已連續 {twse_only_fails} 天抓不到資料，疑似被證交所暫時封鎖"
        elif consecutive_empty >= MAX_EMPTY_DAYS:
            aborted = f"已連續 {consecutive_empty} 個平日完全沒有資料，疑似網路或來源異常"
        if aborted:
            break

        if i < len(days):
            time.sleep(SLEEP_BETWEEN_DAYS)
            if i % SLEEP_BATCH_EVERY == 0:
                print(f"\n⏸️  已完成 {i}/{len(days)} 天，休息 {SLEEP_BATCH_DURATION} 秒後繼續...")
                time.sleep(SLEEP_BATCH_DURATION)

    # ---------- 結果 ----------
    summary = [
        f"## 區間回補：{start} ～ {end}",
        f"- 資料：{names}",
        f"- 有寫入資料：{len(filled_days)} 天",
        f"- 沒有資料：{len(empty_days)} 天（休市日屬正常）{'：' + '、'.join(empty_days) if empty_days else ''}",
    ]
    if problems:
        summary.append(f"- ❌ 有問題：{len(problems)} 項")
        summary.extend(f"  - {p}" for p in problems)
    if aborted:
        resume = days[max(0, i - max(twse_only_fails, consecutive_empty))]
        summary.append(f"- ⛔ 提早停止：{aborted}。請過一段時間後，從 {resume} 重新開始。")
    if not problems and not aborted:
        summary.append("- ✅ 全部完成")

    print("\n" + "\n".join(summary))
    write_summary(summary)
    return 1 if (problems or aborted) else 0


if __name__ == "__main__":
    sys.exit(main())
