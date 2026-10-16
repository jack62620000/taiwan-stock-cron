"""
還原權值事件 - 抓取除權息、減資、變更面額（分割）的「事件前收盤價」與「參考價」

每筆事件存成 price_adjustments 的一列，調整比例 factor = 參考價 ÷ 事件前收盤價。
網頁計算技術指標時，把事件日之前的股價乘上 factor，前後股價就能連續。

資料來源（2026-10 實測）：
  除權息    上市：證交所 TWT49U          上櫃：櫃買中心 exDailyQ
  減資      上市：證交所 TWTAUU          上櫃：櫃買中心 revivt
  變更面額  上市：證交所 TWTB8U          上櫃：櫃買中心沒有可用的公開網址，改用 FinMind（免費）

auto_fetch.py 每天抓最近兩週；backfill.py 用 DATASETS=adjust 回補指定區間。
直接執行這個檔案並設 DRY_RUN=1，只會印出抓到的事件，不寫入資料庫。
"""
import os
import sys
import time
from datetime import date, datetime, timedelta

import requests
import urllib3

urllib3.disable_warnings()

TABLE = "price_adjustments"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
SLEEP_BETWEEN_API = 3


# ==================== 小工具 ====================
def _num(s):
    """'1,490.00' → 1490.0；空白、'--' 等無法轉換的回傳 None"""
    try:
        v = float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _roc_date(s):
    """民國日期轉西元：'115年08月03日'、'115/08/03'、'1150825' → '2026-08-03'"""
    s = str(s).strip()
    for ch in ("年", "月"):
        s = s.replace(ch, "/")
    s = s.replace("日", "")
    if "/" in s:
        y, m, d = s.split("/")
    elif len(s) == 7:
        y, m, d = s[:3], s[3:5], s[5:]
    else:
        return None
    try:
        return date(int(y) + 1911, int(m), int(d)).isoformat()
    except ValueError:
        return None


def _months(start, end):
    """把區間切成一個月一段（來源一次查太長可能被拒絕）"""
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    while s <= e:
        nxt = (s.replace(day=1) + timedelta(days=32)).replace(day=1)
        yield s, min(e, nxt - timedelta(days=1))
        s = nxt


def _get_json(url):
    r = requests.get(url, headers=HEADERS, timeout=30, verify=False)
    r.raise_for_status()
    return r.json()


def _record(stock_id, day, event_type, prev_close, ref_price, source):
    stock_id = str(stock_id).strip()
    if not stock_id or not day or not prev_close or not ref_price:
        return None
    return {
        "stock_id": stock_id,
        "date": day,
        "event_type": event_type,
        "prev_close": prev_close,
        "ref_price": ref_price,
        "factor": round(ref_price / prev_close, 8),
        "source": source,
    }


# ==================== 證交所（上市）====================
# (網址代號, 事件類型, 日期欄, 代號欄, 前收盤欄, 參考價欄)
# 除權息用「減除股利參考價」（空白時退回除權息參考價）：只扣掉股利與無償配股，不含現金增資認購。
# 單純現金增資的除權，證交所仍以前收盤價為開盤基準，若用「除權息參考價」會把股價錯誤調低。
TWSE_REPORTS = [
    ("exRight/TWT49U",   "除權息",  0, 1, 3, 10),
    ("reducation/TWTAUU", "減資",    0, 1, 3, 4),
    ("change/TWTB8U",    "變更面額", 0, 1, 3, 4),
]


def fetch_twse(s, e):
    out = []
    for path, event, i_date, i_id, i_prev, i_ref in TWSE_REPORTS:
        url = (f"https://www.twse.com.tw/rwd/zh/{path}"
               f"?startDate={s:%Y%m%d}&endDate={e:%Y%m%d}&response=json")
        data = _get_json(url)
        if data.get("stat") != "OK":
            # 沒有事件時 stat 會是「很抱歉，沒有符合條件的資料」之類的訊息，屬正常
            time.sleep(SLEEP_BETWEEN_API)
            continue
        for row in data.get("data", []):
            rec = _record(row[i_id], _roc_date(row[i_date]), event,
                          _num(row[i_prev]), _num(row[i_ref]) or _num(row[4]), "證交所")
            # 變更面額報表可能不照查詢區間回傳，超出區間的不收
            if rec and s.isoformat() <= rec["date"] <= e.isoformat():
                out.append(rec)
        time.sleep(SLEEP_BETWEEN_API)
    return out


# ==================== 櫃買中心（上櫃）====================
TPEX_REPORTS = [
    ("exDailyQ", "除權息", 0, 1, 3, 12),  # 減除股利參考價，理由同上
    ("revivt",   "減資",   0, 1, 3, 4),
]


def fetch_tpex(s, e):
    out = []
    for path, event, i_date, i_id, i_prev, i_ref in TPEX_REPORTS:
        url = (f"https://www.tpex.org.tw/www/zh-tw/bulletin/{path}"
               f"?startDate={s:%Y/%m/%d}&endDate={e:%Y/%m/%d}&response=json")
        data = _get_json(url)
        for table in data.get("tables", []):
            for row in table.get("data", []):
                rec = _record(row[i_id], _roc_date(row[i_date]), event,
                              _num(row[i_prev]), _num(row[i_ref]) or _num(row[4]), "櫃買中心")
                if rec and s.isoformat() <= rec["date"] <= e.isoformat():
                    out.append(rec)
        time.sleep(SLEEP_BETWEEN_API)
    return out


# ==================== FinMind（上櫃變更面額）====================
def fetch_finmind_par_value(s, e):
    url = ("https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockSplitPrice"
           f"&start_date={s.isoformat()}&end_date={e.isoformat()}")
    data = _get_json(url)
    if data.get("status") != 200:
        raise RuntimeError(f"FinMind 回應異常：{data.get('msg')}")
    out = []
    for row in data.get("data", []):
        rec = _record(row.get("stock_id"), row.get("date"), "變更面額",
                      _num(row.get("before_price")), _num(row.get("after_price")), "FinMind")
        if rec:
            out.append(rec)
    time.sleep(SLEEP_BETWEEN_API)
    return out


# ==================== 對外介面 ====================
def fetch_range(start, end, valid_stocks=None):
    """
    抓 start～end（YYYY-MM-DD）期間的所有事件。回傳 (records, problems)。
    同一檔、同一天、同一類事件只留一筆，官方來源優先於 FinMind。
    """
    records, problems = [], []
    for s, e in _months(start, end):
        print(f"  還原權值事件 {s} ～ {e}")
        for label, fn in (("FinMind 變更面額", fetch_finmind_par_value),
                          ("證交所", fetch_twse), ("櫃買中心", fetch_tpex)):
            try:
                got = fn(s, e)
                records.extend(got)
            except Exception as ex:
                problems.append(f"{s:%Y-%m} {label}：{type(ex).__name__}: {ex}")
                print(f"    ❌ {label} 抓取失敗：{ex}")

    by_key = {}
    for r in records:  # 後面的覆蓋前面的：官方來源排在 FinMind 之後
        if valid_stocks and r["stock_id"] not in valid_stocks:
            continue
        by_key[(r["stock_id"], r["date"], r["event_type"])] = r
    clean = sorted(by_key.values(), key=lambda r: (r["date"], r["stock_id"]))
    print(f"  共 {len(clean)} 筆事件（除權息 {sum(r['event_type'] == '除權息' for r in clean)}、"
          f"減資 {sum(r['event_type'] == '減資' for r in clean)}、"
          f"變更面額 {sum(r['event_type'] == '變更面額' for r in clean)}）")
    return clean, problems


def main():
    """試跑：DRY_RUN=1 START_DATE=... END_DATE=... python fetch_adjustments.py"""
    start = os.getenv("START_DATE", "2025-09-01")
    end = os.getenv("END_DATE", "") or date.today().isoformat()
    records, problems = fetch_range(start, end)
    for r in records:
        if not 0.97 <= r["factor"] <= 1.0:  # 只列出影響較大的事件，一般小額除息省略
            print(f"    {r['date']} {r['stock_id']:<6} {r['event_type']:<4} "
                  f"{r['prev_close']:>10} → {r['ref_price']:<10} 比例 {r['factor']:.4f}（{r['source']}）")
    bad = [r for r in records if r["factor"] > 20 or r["factor"] < 0.02]
    print(f"\n比例異常（>20 或 <0.02）：{len(bad)} 筆")
    if problems:
        print("\n❌ 有問題：")
        for p in problems:
            print(f"   - {p}")
    if os.getenv("DRY_RUN", "") != "1":
        import fetch_stock_info as f
        _, failed = f.save_data_batches(TABLE, records)
        return 1 if (failed or problems) else 0
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
