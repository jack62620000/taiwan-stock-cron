"""
自動化版本 - 給 GitHub Actions 排程使用
每次執行會檢查最近 7 天，把還沒存齊的交易日的股價、三大法人（含外資持股）、融資融券
（餘額＋限額＋使用率）補抓起來，再更新最近兩週的還原權值事件、當月的大盤指數；
每月 1～20 號另外補抓上個月還沒存到的月營收。

抓取函式全部來自 fetch_stock_info.py，這裡不再複製一份，
之後只要改 fetch_stock_info.py，排程就會跟著更新。

有資料缺漏或寫入失敗時會以錯誤碼結束，GitHub 會把這次執行標成失敗並寄信通知。
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import fetch_adjustments as adj
import fetch_foreign_holding as foreign
import fetch_market_index as market
import fetch_revenue_history as revenue
import fetch_stock_info as f

SLEEP_BETWEEN_API = 2
ADJUST_LOOKBACK_DAYS = 14   # 還原權值事件每天重抓最近兩週，漏抓一兩天也會補上
# 月營收法定期限是次月 10 日（遇假日順延），晚報的公司陸續補上，所以 1～20 號每天補抓上個月
REVENUE_UNTIL_DAY = 20
REVENUE_DEADLINE_DAY = 10  # 這天之前彙總表可能還沒出來，抓不到不算錯誤
CATCHUP_DAYS = 7           # 每次檢查最近 7 天，排程漏跑或延遲的日子會自動補上
ANCHOR_STOCKS = ("2330", "6488")  # 上市、上櫃各一檔幾乎每天都有交易的股票，用來判斷某天是否已存齊


def taiwan_today():
    return datetime.now(timezone(timedelta(hours=8))).date()


def missing_dates(today, days=CATCHUP_DAYS):
    """
    最近 days 天（含今天）的平日裡，哪幾天的股價、法人、融資券還沒存齊。
    用上市 2330、上櫃 6488 當代表：任一檔在任一張表缺資料，就算那天沒存齊。
    國定假日也會被列出來，但抓的時候兩個市場都沒有行情，會直接略過，不算錯誤。
    """
    start = today - timedelta(days=days - 1)
    weekdays = [(start + timedelta(days=i)).isoformat() for i in range(days)
                if (start + timedelta(days=i)).weekday() < 5]
    if not weekdays:
        return []
    have = None
    for table in ("daily_prices", "stock_ins_daily", "margin_utilization"):
        res = f.supabase.table(table).select("stock_id, date").in_("stock_id", list(ANCHOR_STOCKS)) \
            .gte("date", weekdays[0]).lte("date", weekdays[-1]).execute()
        got = {(r["stock_id"], r["date"]) for r in res.data}
        have = got if have is None else have & got
    return [d for d in weekdays if any((sid, d) not in have for sid in ANCHOR_STOCKS)]


def target_dates():
    """
    決定要抓哪幾天。
    - 設了環境變數 TARGET_DATE（YYYY-MM-DD）就只抓那一天，用來手動補抓。
    - 否則抓最近 CATCHUP_DAYS 天裡還沒存齊的交易日。GitHub 排程常延遲好幾個小時，
      甚至拖到隔天或整次沒跑；改成「缺哪天補哪天」，就不用靠執行當下的日期猜要抓哪天。
    """
    override = os.getenv("TARGET_DATE", "").strip()
    if override:
        datetime.strptime(override, "%Y-%m-%d")  # 格式錯誤就直接報錯
        return [override]
    return missing_dates(taiwan_today())


def fetch_day(day, valid_stocks):
    """抓一個交易日的股價、法人（含外資持股）、融資券並寫入。回傳 (是否有行情, 問題清單)"""
    print(f"\n========== {day} ==========")
    print("--- 下載每日股價 ---")
    twse_prices = f.fetch_twse_daily_all(day, valid_stocks)
    time.sleep(SLEEP_BETWEEN_API)
    tpex_prices = f.fetch_tpex_otc_daily(day, valid_stocks)
    time.sleep(SLEEP_BETWEEN_API)

    # 上市、上櫃都沒有行情才視為休市（國定假日、颱風假），或當天資料還沒公布
    if not twse_prices and not tpex_prices:
        print(f"ℹ️ {day} 上市、上櫃都沒有行情（休市或還沒公布），不寫入任何資料。")
        return False, []

    print("--- 下載三大法人 ---")
    twse_ins = f.fetch_twse_stock_ins(day, valid_stocks)
    time.sleep(SLEEP_BETWEEN_API)
    tpex_ins = f.fetch_tpex_stock_ins(day, valid_stocks)
    time.sleep(SLEEP_BETWEEN_API)

    print("--- 下載外資持股 ---")
    holdings, holding_problems = foreign.fetch_day(day, valid_stocks)
    if holdings:  # 整天都抓不到時不併入，避免把已存的持股蓋成空值
        foreign.merge_into(twse_ins + tpex_ins, holdings)

    print("--- 下載融資融券 ---")
    twse_margin = f.fetch_twse_margin(day, valid_stocks)
    time.sleep(SLEEP_BETWEEN_API)
    tpex_margin = f.fetch_tpex_margin(day, valid_stocks)

    fetched = {
        "上市股價": twse_prices, "上櫃股價": tpex_prices,
        "上市法人": twse_ins,    "上櫃法人": tpex_ins,
        "上市融資券": twse_margin, "上櫃融資券": tpex_margin,
    }
    problems = [f"{day} {name}沒有抓到資料" for name, records in fetched.items() if not records]
    problems.extend(f"{day} {p}" for p in holding_problems)

    print("--- 寫入資料庫 ---")
    for table, records in [
        ("daily_prices",       twse_prices + tpex_prices),
        ("stock_ins_daily",    twse_ins + tpex_ins),
        ("margin_utilization", twse_margin + tpex_margin),
    ]:
        _, failed = f.save_data_batches(table, records)
        if failed:
            problems.append(f"{day} {table} 有 {failed} 筆寫入失敗")
    return True, problems


def write_output(**values):
    """把結果交給 GitHub Actions 的下一個步驟（評分只需要從最早補抓的那天開始算）"""
    path = os.getenv("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for k, v in values.items():
            fh.write(f"{k}={v}\n")


def main():
    days = target_dates()
    ref = days[-1] if os.getenv("TARGET_DATE", "").strip() else taiwan_today().isoformat()
    print(f"========== 自動排程執行（台灣時間 {taiwan_today()}）==========")
    if not days:
        print("最近幾天的股價、法人、融資券都已存齊，不需要補抓。")
    else:
        print(f"要抓的日期：{'、'.join(days)}")

    print("\n--- 更新個股基本資訊 ---")
    f.refresh_stock_info()
    valid_stocks = f.get_valid_stock_ids()
    if not valid_stocks:
        print("❌ 無法取得股票清單，中止執行。")
        return 1
    print(f"資料庫內現有 {len(valid_stocks)} 檔股票可供對應。")

    problems, traded = [], []
    for day in days:
        ok, day_problems = fetch_day(day, valid_stocks)
        problems.extend(day_problems)
        if ok:
            traded.append(day)
    write_output(fetched=len(traded), start=traded[0] if traded else "")

    print("\n--- 還原權值事件（除權息、減資、變更面額）---")
    adj_start = (datetime.strptime(ref, "%Y-%m-%d") - timedelta(days=ADJUST_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    events, adj_problems = adj.fetch_range(adj_start, ref, valid_stocks)
    problems.extend(adj_problems)
    if events:
        _, failed = f.save_data_batches(adj.TABLE, events)
        if failed:
            problems.append(f"{adj.TABLE} 有 {failed} 筆寫入失敗")

    print("\n--- 大盤指數（重抓當月）---")
    _, index_problems = market.fetch_range(ref[:8] + "01", ref)
    problems.extend(index_problems)

    day = int(ref[8:10])
    if day <= REVENUE_UNTIL_DAY:
        last_month = (datetime.strptime(ref[:8] + "01", "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"\n--- 月營收（補抓 {last_month[:7]} 還沒存到的公司）---")
        _, rev_problems = revenue.fetch_range(last_month, last_month, valid_stocks)
        if day <= REVENUE_DEADLINE_DAY:
            rev_problems = [p for p in rev_problems if "彙總表沒有資料" not in p]
        problems.extend(rev_problems)

    if problems:
        print("\n❌ 這次同步不完整：")
        for p in problems:
            print(f"   - {p}")
        print("   下一次排程會自動補抓缺的日子；也可以到 Actions 手動執行，在 target_date 填入日期補抓。")
        return 1

    print("\n✅ 自動排程執行完畢！")
    return 0


if __name__ == "__main__":
    sys.exit(main())
