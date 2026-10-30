"""
台股 ETF 排程與回補（GitHub Actions：.github/workflows/etf_update.yml）

用環境變數 ETF_JOB 選擇工作：
  daily    平日盤後：ETF 清單、每日價量、三大法人、融資融券、借券賣出、淨值折溢價、配息；並補最近幾個交易日的缺漏，
           最後清除超過保留期限的舊資料（先封存到 ETF 資料庫的 Storage 再刪除，期限見 RETENTION）
  weekly   週六：集保持股分級（開放資料最新一週）
  monthly  每月：投信公會月資料與前十大持股（重抓最近兩個月），並補上上櫃 ETF 的基金統編；
           各項費用比率（重抓最新月份，補今年與去年還沒有的月、季、年）
  backfill 歷史回補：DATASETS 選 prices,ins,margin,sbl,monthly,top10,fees,holder,dividends（逗號分隔），
           區間 BACKFILL_START～BACKFILL_END
           （上櫃 ETF 的歷史配息來源只有預告表，回補只含上市 ETF）
其他設定：
  TARGET_DATE   daily 要抓的日期（YYYY-MM-DD，留空為台灣今天；排程拖到隔天下午 3 點前才跑時抓前一天）
  ETF_DRY_RUN=1 只下載與解析、印出筆數，不讀寫資料庫（驗證來源用）
資料寫入 ETF 專用資料庫（ETF_SUPABASE_URL / ETF_SUPABASE_KEY）。最後檢查筆數，明顯不足時以錯誤碼結束，
讓 GitHub 把這次排程標成失敗。
"""
import os
import sys
import time
from datetime import date, timedelta

import etf_fetch as f

DRY_RUN = os.getenv("ETF_DRY_RUN") == "1"
# 一個交易日正常約 360 檔（上市約 240＋上櫃約 120），低於這個數字視為不完整
MIN_DAILY_ROWS = 300
MIN_TOP10_ROWS = 2000   # 前十大持股一個月正常約 300 檔 × 10 名
MIN_FEE_ROWS = 150      # 費用率一期 2021 年約 180 檔、2026 年約 340 檔
FEE_FIRST_YEAR = 2021   # 公會費用率資料從 2021 年 9 月起
MIN_WEEKLY_ROWS = 300
MIN_MONTHLY_ROWS = 250
# daily 順便檢查最近幾個交易日，前幾天排程延遲或失敗時補上
RECENT_TRADING_DAYS = 5
# 回補單次執行的時間上限（GitHub Actions 單一工作最多 6 小時），到了就停下，下次用同一區間接續
BACKFILL_MAX_MINUTES = 320
# 盤後資料約下午 3 點後才陸續公布；daily 在這之前執行（晚上的排程被 GitHub 延遲過午夜）時改抓前一天
DATA_READY_HOUR = 15

# 保留期限和個股相同（見 cleanup_old_data.py）：(資料表, 日期欄位, 保留年數)
# 價量 3 年、法人 2 年、集保 2 年；淨值是每日資料，比照價量 3 年；配息、月資料、ETF 清單是小表，全部保留
RETENTION = [
    ("etf_daily_prices", "date", 3),
    ("etf_nav", "date", 3),
    ("etf_ins_daily", "date", 2),
    ("etf_holder_distribution", "week_date", 2),
    ("etf_margin_daily", "date", 1),            # 個股融資融券保留 1 年
    ("etf_sbl_daily", "date", 1),               # 借券和融資融券同屬信用交易，比照保留 1 年
    ("etf_top_holdings", "year_month", 3),      # 前十大持股比照價量 3 年
]
ANCHOR_ETF = "0050"     # 每天都有交易，用它找出過期的日期
MAX_DAYS_PER_RUN = 10   # 每張表每次最多刪這麼多天
ARCHIVE_BUCKET = "archive"


def save(table, records):
    return f.save(table, records, dry_run=DRY_RUN)


# ==================== daily ====================
def fetch_prices_and_ins(day, with_list=False, with_prices=True, with_ins=True):
    """抓一天的上市＋上櫃價量與法人並寫入，回傳 (價量筆數, 法人筆數, ETF 清單列)"""
    problems = []
    twse, tpex_rows = [], []
    if with_prices or with_list:
        try:
            twse = f.fetch_twse_prices(day)
        except Exception as e:
            problems.append(f"上市行情：{e}")
        try:
            tpex_rows = f.fetch_tpex_price_rows(day)
        except Exception as e:
            problems.append(f"上櫃行情：{e}")
    prices = twse + f.parse_tpex_prices(tpex_rows, day)
    ins = []
    # 只補法人時不先抓行情；休市日法人來源本來就會回傳空資料
    if with_ins and (prices or not with_prices):
        for label, fn in (("上市法人", f.fetch_twse_ins), ("上櫃法人", f.fetch_tpex_ins)):
            try:
                ins += fn(day)
            except Exception as e:
                problems.append(f"{label}：{e}")
    for p in problems:
        print(f"⚠️ {day} {p}")
    if with_prices:
        save("etf_daily_prices", prices)
    if with_ins:
        save("etf_ins_daily", ins)
    listing = f.tpex_etf_list(tpex_rows, day) if with_list else []
    return len(prices), len(ins), listing


def update_nav():
    """MIS 當天快照寫入 etf_nav，並用「前一營業日淨值」回寫前一天的正式淨值；回傳 (資料日, 筆數)"""
    rows, prev_nav = f.fetch_mis_nav()
    if not rows:
        print("⚠️ MIS 沒有回傳 ETF 淨值")
        return None, 0
    day = max(r["date"] for r in rows)
    rows = [r for r in rows if r["date"] == day]
    save("etf_nav", rows)
    if prev_nav and not DRY_RUN:
        res = f.db().table("etf_nav").select("date").lt("date", day).order("date", desc=True).limit(1).execute()
        if res.data:
            prev = res.data[0]["date"]
            save("etf_nav", [{"etf_id": k, "date": prev, "nav": v} for k, v in prev_nav.items()])
            print(f"已回寫 {prev} 的正式淨值 {len(prev_nav)} 檔")
    return day, len(rows)


def fill_recent_gaps(day):
    """最近幾個交易日如果價量或法人筆數不足，重抓那一天"""
    if DRY_RUN:
        return []
    start = (date.fromisoformat(day) - timedelta(days=14)).isoformat()
    days = [d for d in f.trading_days(start, day) if d < day][-RECENT_TRADING_DAYS:]
    problems = []
    for d in days:
        n_price, n_ins = f.count_rows("etf_daily_prices", date=d), f.count_rows("etf_ins_daily", date=d)
        if n_price >= MIN_DAILY_ROWS and n_ins >= MIN_DAILY_ROWS:
            continue
        print(f"\n--- 補抓 {d}（價量 {n_price} 筆、法人 {n_ins} 筆）---")
        n_price, n_ins, _ = fetch_prices_and_ins(d)
        if n_price and (n_price < MIN_DAILY_ROWS or n_ins < MIN_DAILY_ROWS):
            problems.append(f"{d} 補抓後仍不完整：價量 {n_price}、法人 {n_ins}")
    return problems


CREDIT_TABLES = [("etf_margin_daily", "融資融券", lambda d: f.fetch_margin(d)),
                 ("etf_sbl_daily", "借券賣出", lambda d: f.fetch_sbl(d))]


def fill_recent_credit(day):
    """融資融券、借券賣出較晚公布：排程太早跑時當天會缺，下一次每日排程補抓最近幾個交易日"""
    if DRY_RUN:
        return []
    start = (date.fromisoformat(day) - timedelta(days=14)).isoformat()
    days = [d for d in f.trading_days(start, day) if d < day][-RECENT_TRADING_DAYS:]
    problems = []
    for d in days:
        if f.count_rows("etf_daily_prices", date=d) < MIN_DAILY_ROWS:
            continue  # 休市或價量也缺，交給 fill_recent_gaps
        for table, label, fetch in CREDIT_TABLES:
            if f.count_rows(table, date=d) >= MIN_DAILY_ROWS:
                continue
            print(f"\n--- 補抓 {d} {label} ---")
            try:
                n = save(table, fetch(d))[0]
            except Exception as e:
                n = 0
                print(f"⚠️ {d} {label}：{e}")
            if n < MIN_DAILY_ROWS:
                problems.append(f"{d} {label}補抓後只有 {n} 筆")
    return problems


def run_daily(day):
    print(f"========== ETF 每日資料 {day} ==========")
    if not f.trading_days(day, day):
        print(f"{day} 不是交易日，略過價量與法人")
        n_price = n_ins = None
        listing = []
    else:
        n_price, n_ins, listing = fetch_prices_and_ins(day, with_list=True)

    problems = []
    try:
        twse_list = f.fetch_twse_etf_list(day)
        save("etf_info", twse_list + [x for x in listing if x["etf_id"] not in {r["etf_id"] for r in twse_list}])
    except Exception as e:
        problems.append(f"ETF 清單：{e}")

    try:
        nav_day, n_nav = update_nav()
    except Exception as e:
        nav_day, n_nav = None, 0
        problems.append(f"MIS 淨值：{e}")

    dividends = f.fetch_dividends()
    save("etf_dividends", dividends)
    if not dividends:
        problems.append("配息三個來源都沒有取得資料")
    problems += fill_recent_gaps(day)

    n_margin = n_sbl = None
    if n_price is not None:
        try:
            n_margin = save("etf_margin_daily", f.fetch_margin(day))[0]
        except Exception as e:
            problems.append(f"融資融券：{e}")
        try:
            n_sbl = save("etf_sbl_daily", f.fetch_sbl(day))[0]
        except Exception as e:
            problems.append(f"借券賣出：{e}")
        problems += fill_recent_credit(day)
    problems += cleanup_expired(f.today_taipei())

    if n_price is not None:
        if n_price < MIN_DAILY_ROWS:
            problems.append(f"{day} 價量只有 {n_price} 筆（來源可能尚未更新或被擋）")
        if n_ins < MIN_DAILY_ROWS:
            problems.append(f"{day} 法人只有 {n_ins} 筆")
        # 當天 0 筆通常是還沒公布，下一次每日排程會補抓；有資料但不足才算問題
        if n_margin and n_margin < MIN_DAILY_ROWS:
            problems.append(f"{day} 融資融券只有 {n_margin} 筆")
        if n_sbl and n_sbl < MIN_DAILY_ROWS:
            problems.append(f"{day} 借券賣出只有 {n_sbl} 筆")
        if nav_day != day or n_nav < MIN_DAILY_ROWS:
            problems.append(f"MIS 淨值資料日 {nav_day}、{n_nav} 筆，不是 {day} 的完整資料")
    return problems


# ==================== weekly / monthly ====================
def run_weekly():
    print("========== ETF 集保持股分級 ==========")
    rows = f.fetch_tdcc_holders()
    save("etf_holder_distribution", rows)
    if not rows:
        return ["集保開放資料沒有 ETF 資料"]
    week = max(r["week_date"] for r in rows)
    n = sum(1 for r in rows if r["week_date"] == week)
    print(f"最新一週 {week}：{n} 檔")
    problems = []
    if n < MIN_WEEKLY_ROWS:
        problems.append(f"最新一週 {week} 只有 {n} 檔")
    if (date.today() - date.fromisoformat(week)).days > 8:
        problems.append(f"最新一週是 {week}，已超過 8 天沒有新資料")
    return problems


def tax_id_map():
    """基金統編 → 代號（來自 etf_info）"""
    if DRY_RUN:
        return {}
    return {r["fund_tax_id"]: r["etf_id"] for r in f.select_all("etf_info", "etf_id, fund_tax_id")
            if r.get("fund_tax_id")}


def fill_tax_ids(tax_ids):
    """上櫃 ETF 沒有基金統編，用投信公會資料補上（之後回補舊月份才對得回代號）"""
    if DRY_RUN or not tax_ids:
        return
    missing = [r["etf_id"] for r in f.select_all("etf_info", "etf_id, fund_tax_id") if not r.get("fund_tax_id")]
    filled = 0
    for code in missing:
        if code in tax_ids:
            f.db().table("etf_info").update({"fund_tax_id": tax_ids[code]}).eq("etf_id", code).execute()
            filled += 1
    if filled:
        print(f"已補上 {filled} 檔 ETF 的基金統編")


def recent_months(n, today=None):
    d = (today or f.today_taipei()).replace(day=1)
    out = []
    for _ in range(n):
        d = (d - timedelta(days=1)).replace(day=1)
        out.append(d.strftime("%Y-%m"))
    return out


def run_monthly():
    print("========== ETF 投信公會月資料、前十大持股 ==========")
    problems, by_tax = [], tax_id_map()
    months = recent_months(2)
    for ym in months:
        html = f.fetch_sitca_statement(ym)
        rows, tax_ids = f.parse_sitca_monthly(html, ym, by_tax)
        print(f"{ym}：{len(rows)} 檔")
        if not rows:
            if ym == months[1]:
                problems.append(f"{ym} 投信公會沒有資料")
            continue
        save("etf_monthly_stats", rows)
        fill_tax_ids(tax_ids)
        by_tax.update({tax: code for code, tax in tax_ids.items()})
        top10 = f.fetch_sitca_top10(ym, f.parse_sitca_names(html, by_tax))
        save("etf_top_holdings", top10)
        if len(top10) < MIN_TOP10_ROWS and ym == months[1]:
            problems.append(f"{ym} 前十大持股只有 {len(top10)} 筆")
    this_year = f.today_taipei().year
    problems += fetch_fees([this_year - 1, this_year], by_tax, refresh_latest=True)
    return problems


def fee_tax_map():
    """基金統編 → 代號：etf_info 不是每檔都有統編，再用投信公會最近一個月的月資料補齊"""
    by_tax = tax_id_map()
    _, tax_ids = f.fetch_sitca_monthly(recent_months(2)[1])
    for code, tax in tax_ids.items():
        by_tax.setdefault(tax, code)
    return by_tax


def fetch_fees(years, by_tax, deadline=None, refresh_latest=False):
    """
    各項費用比率：逐年切換後，查這一年選單裡的月、季、年（今年的「全年」是累計到目前，不抓）。
    refresh_latest（每月排程）：資料庫已有足夠筆數的期間略過，只有最新一個月一律重抓（公會可能晚補資料）；
    回補時全部重抓。
    """
    problems, this_year = [], f.today_taipei().year
    for year in years:
        hidden, offered = f.open_sitca_fee_year(year)
        if not offered:
            if year != this_year:  # 年初公會還沒有今年的資料，正常
                problems.append(f"投信公會費用率：無法切換到 {year} 年")
            continue
        for mv in offered:
            if mv == "Year" and year == this_year:
                continue
            period, _ = f.fee_period(year, mv)
            is_latest = refresh_latest and mv == offered[-1] and year == years[-1]
            if refresh_latest and not DRY_RUN and not is_latest and f.count_rows("etf_expense_ratio", period=period) >= MIN_FEE_ROWS:
                continue
            if deadline and time.time() > deadline:
                return problems + [f"已到執行時間上限，{period} 起的費用率還沒補"]
            try:
                rows = f.fetch_sitca_fees(year, mv, by_tax, hidden)
            except Exception as e:
                problems.append(f"{period} 費用率：{e}")
                continue
            print(f"{period}：{len(rows)} 檔")
            save("etf_expense_ratio", rows)
            if len(rows) < MIN_FEE_ROWS:
                problems.append(f"{period} 費用率只有 {len(rows)} 檔")
            if DRY_RUN:
                return problems
    return problems


# ==================== 清除過期資料 ====================
def cleanup_expired(today):
    """各表超過保留期限的資料先封存再刪除，一次處理一天、每表最多 MAX_DAYS_PER_RUN 天。回傳問題清單"""
    from cleanup_old_data import years_ago
    if DRY_RUN:
        return []
    problems = []
    try:
        f.ensure_bucket(ARCHIVE_BUCKET)
    except Exception as e:
        return [f"清除過期資料：無法建立封存用的 Storage 桶（{e}）"]
    for table, col, years in RETENTION:
        cutoff = years_ago(today, years).isoformat()
        try:
            days = [r[col] for r in f.db().table(table).select(col).eq("etf_id", ANCHOR_ETF)
                    .lt(col, cutoff).order(col).limit(MAX_DAYS_PER_RUN).execute().data]
            total = 0
            for d in days:
                f.archive_day(table, col, d, ARCHIVE_BUCKET)  # 封存失敗會丟出錯誤，這天就不會被刪
                res = f.db().table(table).delete(count="exact", returning="minimal").eq(col, d).execute()
                total += res.count or 0
            if days:
                print(f"{table}：保留 {years} 年（{cutoff} 起），封存並刪除 {days[0]}～{days[-1]} 共 {total:,} 筆")
        except Exception as e:
            problems.append(f"{table} 清除過期資料失敗：{e}")
    return problems


# ==================== backfill ====================
def backfill_daily(start, end, deadline, with_prices=True, with_ins=True):
    days = f.trading_days(start, end)
    label = "、".join(x for x, on in (("價量", with_prices), ("法人", with_ins)) if on)
    print(f"{label}回補：{start} ～ {end}，共 {len(days)} 個平日")
    problems = []
    for i, d in enumerate(days):
        if time.time() > deadline:
            return problems + [f"已到執行時間上限，{d} 之後還沒補，請用同一區間再執行一次接續"]
        need_prices = with_prices and (DRY_RUN or f.count_rows("etf_daily_prices", date=d) < MIN_DAILY_ROWS)
        need_ins = with_ins and (DRY_RUN or f.count_rows("etf_ins_daily", date=d) < MIN_DAILY_ROWS)
        if not (need_prices or need_ins):
            continue
        print(f"\n[{i + 1}/{len(days)}] {d}")
        fetch_prices_and_ins(d, with_prices=need_prices, with_ins=need_ins)
    return problems


def backfill_margin(start, end, deadline):
    days = f.trading_days(start, end)
    print(f"融資融券回補：{start} ～ {end}，共 {len(days)} 個平日")
    for i, d in enumerate(days):
        if time.time() > deadline:
            return [f"已到執行時間上限，{d} 之後的融資融券還沒補，請用同一區間再執行一次接續"]
        if not DRY_RUN and f.count_rows("etf_margin_daily", date=d) >= MIN_DAILY_ROWS:
            continue
        print(f"\n[{i + 1}/{len(days)}] {d}")
        try:
            save("etf_margin_daily", f.fetch_margin(d))
        except Exception as e:
            print(f"⚠️ {d} 融資融券：{e}")
    return []


def backfill_sbl(start, end, deadline):
    days = f.trading_days(start, end)
    print(f"借券賣出回補：{start} ～ {end}，共 {len(days)} 個平日")
    for i, d in enumerate(days):
        if time.time() > deadline:
            return [f"已到執行時間上限，{d} 之後的借券賣出還沒補，請用同一區間再執行一次接續"]
        if not DRY_RUN and f.count_rows("etf_sbl_daily", date=d) >= MIN_DAILY_ROWS:
            continue
        print(f"\n[{i + 1}/{len(days)}] {d}")
        try:
            save("etf_sbl_daily", f.fetch_sbl(d))
        except Exception as e:
            print(f"⚠️ {d} 借券賣出：{e}")
    return []


def backfill_top10(start, end, deadline):
    """前十大持股：公會資料 2021-09 起；從新到舊，讓基金統編對照表越來越完整"""
    months = [m for m in reversed(month_range(max(start, "2021-09-01"), end))
              if m < f.today_taipei().strftime("%Y-%m")]
    print(f"前十大持股回補：{len(months)} 個月")
    by_tax, problems = tax_id_map(), []
    for ym in months:
        if time.time() > deadline:
            return problems + [f"已到執行時間上限，{ym} 以前的前十大持股還沒補"]
        if not DRY_RUN and f.count_rows("etf_top_holdings", year_month=ym) >= MIN_TOP10_ROWS:
            continue
        try:
            html = f.fetch_sitca_statement(ym)
            _, tax_ids = f.parse_sitca_monthly(html, ym, by_tax)
            by_tax.update({tax: code for code, tax in tax_ids.items()})
            rows = f.fetch_sitca_top10(ym, f.parse_sitca_names(html, by_tax))
            print(f"{ym}：{len(rows)} 筆")
            save("etf_top_holdings", rows)
        except Exception as e:
            problems.append(f"{ym} 前十大持股：{e}")
        if DRY_RUN:
            break
    return problems


def month_range(start, end):
    y, m = int(start[:4]), int(start[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= end[:7]:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def backfill_monthly(start, end, deadline):
    # 從新到舊：新月份有證券代號，可以學到「基金統編 → 代號」，用來對回舊月份
    months = [m for m in reversed(month_range(start, end)) if m < f.today_taipei().strftime("%Y-%m")]
    print(f"投信公會月資料回補：{len(months)} 個月")
    by_tax, problems = tax_id_map(), []
    for ym in months:
        if time.time() > deadline:
            return problems + [f"已到執行時間上限，{ym} 以前還沒補"]
        try:
            rows, tax_ids = f.fetch_sitca_monthly(ym, by_tax)
        except Exception as e:
            problems.append(f"{ym}：{e}")
            continue
        for code, tax in tax_ids.items():
            by_tax.setdefault(tax, code)
        print(f"{ym}：{len(rows)} 檔")
        save("etf_monthly_stats", rows)
    return problems


def backfill_holder(start, end, deadline):
    q = f.TdccQuery()
    q.open()
    weeks = [w for w in q.weeks if start <= w <= end]
    print(f"集保網頁可查 {len(q.weeks)} 週（{q.weeks[0]} ～ {q.weeks[-1]}），本次區間內 {len(weeks)} 週")
    codes = sorted({r["etf_id"] for r in f.select_all("etf_info", "etf_id")}) if not DRY_RUN else ["0050", "00679B"]
    problems = []
    for i, week in enumerate(reversed(weeks)):
        if time.time() > deadline:
            return problems + [f"已到執行時間上限，{week} 以前還沒補，請用同一區間再執行一次接續"]
        done = set() if DRY_RUN else {r["etf_id"] for r in f.select_all("etf_holder_distribution", "etf_id",
                                                                       week_date=week)}
        todo = [c for c in codes if c not in done]
        print(f"\n[{i + 1}/{len(weeks)}] {week}：需補 {len(todo)} 檔")
        records, fails = [], 0
        for code in todo:
            for attempt in range(3):
                try:
                    summary = q.week_summary(code, week)
                    if summary:
                        records.append({"etf_id": code, "week_date": week, **summary})
                    break
                except Exception:
                    if attempt == 2:
                        fails += 1
                    else:
                        time.sleep(5 * (attempt + 1))
                        try:
                            q.open()
                        except Exception:
                            pass
            time.sleep(0.3)
            if fails >= 20:
                return problems + [f"{week} 連續查詢失敗，疑似被集保網站擋下，先停止"]
        save("etf_holder_distribution", records)
        if DRY_RUN:
            print(records)
            break
    return problems


def backfill_dividends(start, end):
    """證交所 etfDiv 按年查（一次查太長的區間回應很大），只含上市 ETF"""
    problems, rows = [], []
    for year in range(int(start[:4]), int(end[:4]) + 1):
        s, e = max(start, f"{year}-01-01"), min(end, f"{year}-12-31")
        try:
            got = f.fetch_twse_etf_div(s, e)
            print(f"{year} 年配息：{len(got)} 筆")
            rows += got
        except Exception as ex:
            problems.append(f"{year} 年配息：{ex}")
    save("etf_dividends", f.merge_dividends(rows))
    return problems


def run_backfill():
    start = os.getenv("BACKFILL_START") or (f.today_taipei() - timedelta(days=365)).isoformat()
    end = os.getenv("BACKFILL_END") or f.today_taipei().isoformat()
    datasets = [d.strip() for d in (os.getenv("DATASETS") or "prices,ins,monthly,holder,dividends").split(",")
                if d.strip()]
    deadline = time.time() + BACKFILL_MAX_MINUTES * 60
    print(f"========== ETF 歷史回補 {start} ～ {end}：{', '.join(datasets)} ==========")
    problems = []
    if "prices" in datasets or "ins" in datasets:
        problems += backfill_daily(start, end, deadline, "prices" in datasets, "ins" in datasets)
    if "margin" in datasets:
        problems += backfill_margin(start, end, deadline)
    if "sbl" in datasets:
        problems += backfill_sbl(start, end, deadline)
    if "monthly" in datasets:
        problems += backfill_monthly(start, end, deadline)
    if "top10" in datasets:
        problems += backfill_top10(start, end, deadline)
    if "fees" in datasets:
        years = range(max(int(start[:4]), FEE_FIRST_YEAR), int(end[:4]) + 1)
        problems += fetch_fees(list(years), fee_tax_map(), deadline)
    if "dividends" in datasets:
        problems += backfill_dividends(start, end)
    if "holder" in datasets:
        problems += backfill_holder(start, end, deadline)
    return problems


def default_daily_date(now=None):
    """daily 沒指定日期時要抓哪一天：台灣時間下午 3 點前視為還在補前一天的盤後資料"""
    now = now or f.datetime.now(f.TAIPEI)
    day = now.date()
    if now.hour < DATA_READY_HOUR:
        day -= timedelta(days=1)
    return day.isoformat()


if __name__ == "__main__":
    job = os.getenv("ETF_JOB", "daily")
    if DRY_RUN:
        print("（試跑模式：只下載與解析，不讀寫資料庫）")
    if job == "daily":
        problems = run_daily(os.getenv("TARGET_DATE") or default_daily_date())
    elif job == "weekly":
        problems = run_weekly()
    elif job == "monthly":
        problems = run_monthly()
    elif job == "backfill":
        problems = run_backfill()
    else:
        sys.exit(f"不認得的 ETF_JOB：{job}")
    for p in problems:
        print(f"⚠️ {p}")
    if problems:
        print("❌ ETF 資料更新未完成")
        sys.exit(1)
    print("\n✅ ETF 資料更新完成")
