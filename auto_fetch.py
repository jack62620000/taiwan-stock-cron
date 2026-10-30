"""
自動化版本 - 給 GitHub Actions 排程使用
每次執行會檢查最近 7 天，把還沒存齊的交易日（清單出現新股時另外往回重抓到新股上市那天）的股價、三大法人（含外資持股）、融資融券
（餘額＋限額＋使用率）補抓起來，再更新最近兩週的還原權值事件、當月的大盤指數；
每月 1～20 號另外補抓上個月還沒存到的月營收；每天再用開放資料覆蓋最新一個月，公司事後更正營收也會跟上。

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


def recent_weekdays(today, days=CATCHUP_DAYS):
    """最近 days 天（含今天）的平日"""
    start = today - timedelta(days=days - 1)
    return [(start + timedelta(days=i)).isoformat() for i in range(days)
            if (start + timedelta(days=i)).weekday() < 5]


def missing_dates(today, days=CATCHUP_DAYS):
    """
    最近 days 天（含今天）的平日裡，哪幾天的股價、法人、融資券還沒存齊。
    用上市 2330、上櫃 6488 當代表：任一檔在任一張表缺資料，就算那天沒存齊。
    國定假日也會被列出來，但抓的時候兩個市場都沒有行情，會直接略過，不算錯誤。
    """
    weekdays = recent_weekdays(today, days)
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


def fetch_day(day, valid_stocks, need=None):
    """
    抓一個交易日的股價、法人（含外資持股）、融資券並寫入。回傳 (是否有行情, 問題清單)。
    給了 need（新股代號）時，行情裡沒有這些股票就不抓後面的資料，回傳 (None, [])：新股那天還沒上市。
    """
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
    if need and not need & {r["stock_id"] for r in twse_prices + tpex_prices}:
        print(f"ℹ️ {day} 還沒有 {'、'.join(sorted(need))} 的行情（還沒上市），不用重抓。")
        return None, []

    print("--- 下載三大法人 ---")
    twse_ins = f.fetch_twse_stock_ins(day, valid_stocks)
    time.sleep(SLEEP_BETWEEN_API)
    tpex_ins = f.fetch_tpex_stock_ins(day, valid_stocks)
    time.sleep(SLEEP_BETWEEN_API)

    print("--- 下載外資持股 ---")
    holdings, holding_problems = foreign.fetch_day(day, valid_stocks)

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
    # 外資持股另外只寫有抓到的股票：只抓到上市或上櫃其中一邊時，另一邊已存的持股不會被蓋成空值
    ins_ids = {r["stock_id"] for r in twse_ins + tpex_ins}
    hold_rows = [{"stock_id": sid, "date": day, "foreign_hold_shares": s, "foreign_hold_ratio": r}
                 for sid, (s, r) in holdings.items() if sid in ins_ids]
    if hold_rows:
        _, failed = f.save_data_batches(foreign.TABLE, hold_rows)
        if failed:
            problems.append(f"{day} 外資持股有 {failed} 筆寫入失敗")
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
    before = f.get_valid_stock_ids()
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

    # 新股上市當天清單常還沒更新，那天的行情會被略過（2026-10-20 的 6467 泰合就少了第一天）。
    # 清單出現新股時，從最近一天往回重抓，抓到行情裡還沒有新股的那天（還沒上市）就停，不會把 7 天全部重抓。
    # 這些日子原本已經存齊（寫入是 upsert，抓失敗也不會蓋掉已存的資料），所以抓不到只提醒、不算失敗：
    # 2026-10-31 新股 7867 讓程式連續重抓 5 天，證交所擋了幾次請求，資料其實完整卻被標成失敗
    new_stocks = sorted(valid_stocks - before) if before else []
    warnings = []
    if new_stocks and not os.getenv("TARGET_DATE", "").strip():
        extra = sorted(set(recent_weekdays(taiwan_today())) - set(days), reverse=True)
        print(f"\n清單新增 {'、'.join(new_stocks)}，往回重抓到新股上市那天：{'、'.join(extra) or '（最近幾天都已在上面抓過）'}")
        for day in extra:
            ok, day_problems = fetch_day(day, valid_stocks, need=set(new_stocks))
            if ok is None:
                break
            warnings.extend(day_problems)
            if ok:
                traded.append(day)
    write_output(fetched=len(traded), start=min(traded) if traded else "")

    print("\n--- 還原權值事件（除權息、減資、變更面額）---")
    adj_start = (datetime.strptime(ref, "%Y-%m-%d") - timedelta(days=ADJUST_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    events, adj_problems = adj.fetch_range(adj_start, ref, valid_stocks)
    problems.extend(adj_problems)
    if events:
        _, failed = f.save_data_batches(adj.TABLE, events)
        if failed:
            problems.append(f"{adj.TABLE} 有 {failed} 筆寫入失敗")

    print("\n--- 大盤指數（重抓當月，以及這次補抓的日期）---")
    # 排程拖過月底時，ref 已是下個月 1 號；從補抓的第一天或 7 天前開始，月底最後一天才不會漏抓
    week_ago = (datetime.strptime(ref, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    _, index_problems = market.fetch_range(min([ref[:8] + "01", week_ago] + days[:1]), ref)
    problems.extend(index_problems)

    day = int(ref[8:10])
    if day <= REVENUE_UNTIL_DAY:
        last_month = (datetime.strptime(ref[:8] + "01", "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"\n--- 月營收（補抓 {last_month[:7]} 還沒存到的公司）---")
        _, rev_problems = revenue.fetch_range(last_month, last_month, valid_stocks)
        if day <= REVENUE_DEADLINE_DAY:
            rev_problems = [p for p in rev_problems if "彙總表沒有資料" not in p]
        problems.extend(rev_problems)

    # 補抓只抓還沒存到的公司，公司事後更正營收不會更新（抽查發現 2026-09 有 3 家），
    # 所以每天再用開放資料（只有最新一個月，含更正後數字）覆蓋一次；抓不到不算錯誤，隔天會再抓
    print("\n--- 月營收（開放資料覆蓋最新一個月，跟上公司更正）---")
    latest_revenue = f.fetch_monthly_revenue(valid_stocks)
    if latest_revenue:
        _, failed = f.save_data_batches("monthly_revenue", latest_revenue)
        if failed:
            problems.append(f"monthly_revenue 有 {failed} 筆寫入失敗")

    if warnings:
        print("\n⚠️ 新股重抓有幾項沒抓到（這幾天原本的資料都還在，只是新股可能少了那天的部分資料）：")
        for p in warnings:
            print(f"   - {p}")
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
