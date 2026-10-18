"""
週六專用 - 自動補抓最近公布的季報與現金流量

財報申報期限：第一季 5/15、第二季 8/14、第三季 11/14、年度 3/31（金融業稍晚），
各公司會在期限前陸續公布，所以每週六都重抓「季底在最近 200 天內」的季別（通常是兩季）：
  1. 季報（損益表＋資產負債表）：資料庫已有的公司跳過，只補新公布的
  2. 現金流量：只更新資料庫已有季報的公司
都使用公開資訊觀測站的全市場彙總表，跟 backfill.py 的 quarterly、cashflow 相同。

彙總表還沒有資料（季別剛結束、還沒有公司公布）是正常的，不算失敗；
下載或寫入失敗時以錯誤碼結束，GitHub 會把這次排程標成失敗並寄信通知。
設了環境變數 TARGET_DATE（YYYY-MM-DD）就以那天為基準，用來手動補抓。
"""
import os
import sys
from datetime import date, timedelta

import fetch_cashflow_history as cashflow
import fetch_quarterly_history as quarterly
import fetch_stock_info as f

WINDOW_DAYS = 200   # 季底在這麼多天內的季別都重抓
NOT_PUBLISHED = "彙總表沒有資料"


def main():
    override = os.getenv("TARGET_DATE", "").strip()
    today = date.fromisoformat(override) if override else date.today()
    start = (today - timedelta(days=WINDOW_DAYS)).isoformat()
    end = today.isoformat()
    labels = "、".join(f"{y}Q{q}" for y, q in quarterly._quarters(start, end))
    print(f"========== 季報自動更新：{end}｜{labels} ==========")

    valid_stocks = f.get_valid_stock_ids()
    if not valid_stocks:
        print("❌ 無法取得股票清單，中止執行。")
        return 1

    problems = []
    print("\n--- 季報 ---")
    _, quarter_problems = quarterly.fetch_range(start, end, valid_stocks)
    problems.extend(quarter_problems)
    print("\n--- 現金流量 ---")
    _, cash_problems = cashflow.fetch_range(start, end, valid_stocks)
    problems.extend(cash_problems)

    waiting = [p for p in problems if NOT_PUBLISHED in p]
    problems = [p for p in problems if NOT_PUBLISHED not in p]
    for p in waiting:
        print(f"ℹ️ {p}（還沒有公司公布，屬正常）")
    if problems:
        print("\n❌ 季報更新不完整：")
        for p in problems:
            print(f"   - {p}")
        return 1
    print("\n✅ 季報自動更新完畢！")
    return 0


if __name__ == "__main__":
    sys.exit(main())
