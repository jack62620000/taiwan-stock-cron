"""
千張大戶歷史 - 從集保結算所「集保戶股權分散表查詢」逐檔逐週補抓過去的大戶持股比例

每週的開放資料（auto_fetch_holder.py）只有最新一週；查詢網頁可以選過去約一年（51 週）的日期，
但一次只能查一檔股票的一週，所以回補很慢：每週約 2,000 檔，約 20 分鐘。

大戶持股比例 = 持股分級 12～15（400 張以上）的「占集保庫存數比例%」加總，與每週排程相同。
已經有資料的股票和週次會跳過，中途停止後用同一個區間重跑即可接續。

backfill.py 用 DATASETS=holder 回補指定區間。
"""
import re
import time
from datetime import datetime, timedelta

import requests
import urllib3

import fetch_stock_info as f

urllib3.disable_warnings()

TABLE = "holder_distribution"
URL = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
BIG_LEVELS = {"12", "13", "14", "15"}
SLEEP_BETWEEN_QUERY = 0.3
RETRIES = 3
# 連續這麼多檔查詢失敗 → 疑似被擋或網站異常，提早停止
MAX_CONSECUTIVE_FAILS = 20

_ROW = re.compile(
    r"<tr>\s*<td[^>]*>(\d+)</td>\s*<td[^>]*>[^<]*</td>\s*<td[^>]*>[\d,]+</td>"
    r"\s*<td[^>]*>[\d,]+</td>\s*<td[^>]*>([\d.]+)</td>"
)
_TOKEN = re.compile(r'name="SYNCHRONIZER_TOKEN"[^>]*value="([^"]+)"')


class TdccQuery:
    """查詢網頁需要先取得表單代碼，每次查詢的回應會帶下一次要用的代碼"""

    def __init__(self):
        self.session = None
        self.token = ""
        self.weeks = []

    def open(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        r = self.session.get(URL, timeout=30, verify=False)
        r.raise_for_status()
        m = _TOKEN.search(r.text)
        self.token = m.group(1) if m else ""
        self.weeks = sorted(f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in re.findall(r'<option value="(\d{8})"', r.text))

    def big_holder_ratio(self, stock_id, week):
        """回傳該週 400 張以上大戶的持股比例；查無資料回傳 None，連線失敗會丟出例外"""
        d = week.replace("-", "")
        data = {
            "SYNCHRONIZER_TOKEN": self.token, "SYNCHRONIZER_URI": "/portal/zh/smWeb/qryStock",
            "method": "submit", "firDate": d, "scaDate": d, "sqlMethod": "StockNo",
            "stockNo": stock_id, "stockName": "",
        }
        r = self.session.post(URL, data=data, timeout=20, verify=False)
        r.raise_for_status()
        m = _TOKEN.search(r.text)
        if m:
            self.token = m.group(1)
        rows = _ROW.findall(r.text)
        if not rows:
            return None
        return round(sum(float(pct) for level, pct in rows if level in BIG_LEVELS), 2)


def _select_all(build):
    rows, start = [], 0
    while True:
        res = build().range(start, start + 999).execute()
        rows += res.data
        if len(res.data) < 1000:
            return rows
        start += 1000


def stocks_traded_near(week):
    """該週資料日當天或之前最近一個交易日有成交資料的股票（排除那時還沒上市或已下市的）"""
    since = (datetime.strptime(week, "%Y-%m-%d").date() - timedelta(days=10)).isoformat()
    res = f.supabase.table("daily_prices").select("date").gte("date", since).lte("date", week) \
        .order("date", desc=True).limit(1).execute()
    if not res.data:
        return set()
    day = res.data[0]["date"]
    rows = _select_all(lambda: f.supabase.table("daily_prices").select("stock_id").eq("date", day).order("stock_id"))
    return {r["stock_id"] for r in rows}


def stocks_already_saved(week):
    rows = _select_all(lambda: f.supabase.table(TABLE).select("stock_id").eq("week_date", week).order("stock_id"))
    return {r["stock_id"] for r in rows}


def fetch_range(start, end, valid_stocks, deadline=None):
    """
    逐週補抓 start～end 之間、集保網頁查得到的週次，每週查完就寫入資料庫。
    deadline（time.time() 的秒數）到了會先停下來，回報下次從哪一週接續。
    回傳 (寫入筆數, 問題清單, 未完成的週次清單)
    """
    q = TdccQuery()
    q.open()
    weeks = [w for w in q.weeks if start <= w <= end]
    print(f"集保網頁可查 {len(q.weeks)} 週（{q.weeks[0]} ～ {q.weeks[-1]}），本次區間內 {len(weeks)} 週")
    if q.weeks and start < q.weeks[0]:
        print(f"⚠️ {start} ～ {q.weeks[0]} 之前的週次集保網頁已不提供，無法回補")

    saved_total, problems = 0, []
    for i, week in enumerate(weeks):
        if deadline and time.time() > deadline:
            print(f"⏸️ 已到本次執行時間上限，剩下 {len(weeks) - i} 週，請用同一區間再執行一次接續")
            return saved_total, problems, weeks[i:]

        todo = sorted((stocks_traded_near(week) & valid_stocks) - stocks_already_saved(week))
        print(f"\n[{i + 1}/{len(weeks)}] {week}：需補 {len(todo)} 檔")
        if not todo:
            continue

        records, empty, fails, consecutive = [], 0, [], 0
        t0 = time.time()
        for sid in todo:
            for attempt in range(RETRIES):
                try:
                    ratio = q.big_holder_ratio(sid, week)
                except Exception as e:
                    if attempt < RETRIES - 1:
                        time.sleep(5 * (attempt + 1))
                        try:
                            q.open()  # 重新取得表單代碼
                        except Exception:
                            pass
                        continue
                    print(f"  {sid} 查詢失敗：{type(e).__name__}")
                    fails.append(sid)
                    consecutive += 1
                    break
                consecutive = 0
                if ratio is None:
                    empty += 1
                else:
                    records.append({"stock_id": sid, "week_date": week, "big_holder_ratio": ratio})
                break
            if consecutive >= MAX_CONSECUTIVE_FAILS:
                break
            time.sleep(SLEEP_BETWEEN_QUERY)

        saved, failed = f.save_data_batches(TABLE, records)
        saved_total += saved
        print(f"  寫入 {saved} 筆，查無資料 {empty} 檔，查詢失敗 {len(fails)} 檔，耗時 {(time.time() - t0) / 60:.1f} 分鐘")
        if failed:
            problems.append(f"{week} {TABLE}：{failed} 筆寫入失敗")
        if fails:
            problems.append(f"{week} 千張大戶：{len(fails)} 檔查詢失敗（重跑同一區間會補上）")
        if consecutive >= MAX_CONSECUTIVE_FAILS:
            problems.append(f"{week} 起連續 {consecutive} 檔查詢失敗，疑似被集保網站暫時阻擋，提早停止")
            return saved_total, problems, weeks[i:]

    return saved_total, problems, []
