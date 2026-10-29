"""
美股收盤資料 - 每天早上台股開盤前，把前一個美股交易日的收盤存進 Supabase 的 us_market_daily 表，給每日簡報使用

內容：美股四大指數與 VIX、科技七巨頭、AI／半導體焦點股、台灣 ADR、原油、美元指數與美元／台幣、美債殖利率。
資料來源：
  股價、指數、原油、匯率：Yahoo Finance chart API（不用金鑰；原油是近月期貨）
  美債殖利率（2、10、30 年）：美國財政部每日殖利率曲線 CSV（官方資料，比 Yahoo 的 ^TNX 準確）
每次重抓最近 LOOKBACK 天並 upsert，漏跑幾天下次會自動補上；漲跌與漲跌幅用前一個交易日收盤自己算。

寫入 ETF 專用資料庫（另一個 Supabase 專案「台股ETF資料庫」，ETF_SUPABASE_URL / ETF_SUPABASE_KEY），和主資料庫分開。
建表：sql/us_market_daily.sql 貼到那個專案的 SQL Editor 執行一次。
直接執行並設 DRY_RUN=1，只會印出抓到的資料，不寫入資料庫。
"""
import csv
import io
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

TABLE = "us_market_daily"
LOOKBACK = "1mo"           # Yahoo 每次抓的區間
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
RETRIES = 3
SLEEP_BETWEEN_API = 0.5

# (代號, 中文名稱, 分類)；分類給簡報分區塊用
SYMBOLS = [
    ("^DJI", "道瓊工業指數", "index"),
    ("^GSPC", "S&P 500", "index"),
    ("^IXIC", "那斯達克", "index"),
    ("^SOX", "費城半導體", "index"),
    ("^VIX", "VIX 恐慌指數", "index"),
    ("AAPL", "蘋果", "mag7"),
    ("MSFT", "微軟", "mag7"),
    ("NVDA", "輝達", "mag7"),
    ("GOOGL", "Alphabet", "mag7"),
    ("AMZN", "亞馬遜", "mag7"),
    ("META", "Meta", "mag7"),
    ("TSLA", "特斯拉", "mag7"),
    ("AMD", "AMD", "semi"),
    ("AVGO", "博通", "semi"),
    ("MU", "美光", "semi"),
    ("AMAT", "應用材料", "semi"),
    ("INTC", "英特爾", "semi"),
    ("ORCL", "甲骨文", "semi"),
    ("SMCI", "美超微", "semi"),
    ("DELL", "戴爾", "semi"),
    ("VRT", "Vertiv", "semi"),
    ("TSM", "台積電 ADR", "adr"),
    ("ASX", "日月光 ADR", "adr"),
    ("UMC", "聯電 ADR", "adr"),
    ("CHT", "中華電信 ADR", "adr"),
    ("CL=F", "WTI 原油", "commodity"),
    ("BZ=F", "Brent 原油", "commodity"),
    ("DX-Y.NYB", "美元指數", "fx"),
    ("TWD=X", "美元／台幣", "fx"),
]
# 財政部殖利率曲線的欄位 → (代號, 中文名稱)
TREASURY_COLUMNS = {"2 Yr": ("UST2Y", "美國 2 年期公債殖利率"),
                    "10 Yr": ("UST10Y", "美國 10 年期公債殖利率"),
                    "30 Yr": ("UST30Y", "美國 30 年期公債殖利率")}


def _get(url, as_json=True):
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json() if as_json else r.text
        except Exception:
            if attempt == RETRIES - 1:
                raise
            time.sleep(5 * (attempt + 1))


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f   # NaN → None


def with_changes(rows):
    """rows：同一個代號、依日期排序的 dict；用前一筆收盤補上 prev_close、change、change_pct"""
    prev = None
    for r in rows:
        if prev is not None:
            r["prev_close"] = prev
            r["change"] = round(r["close"] - prev, 6)
            r["change_pct"] = round((r["close"] / prev - 1) * 100, 4) if prev else None
        prev = r["close"]
    return rows


def parse_yahoo(data, symbol, name, category):
    """Yahoo chart API 的回傳 → 每日一筆（依日期排序，含漲跌）；日期用交易所當地日期"""
    result = (data.get("chart") or {}).get("result") or []
    if not result:
        return []
    res = result[0]
    stamps = res.get("timestamp") or []
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    offset = (res.get("meta") or {}).get("gmtoffset") or 0
    by_day = {}
    for i, ts in enumerate(stamps):
        close = _num((quote.get("close") or [None] * len(stamps))[i])
        if close is None:
            continue
        day = datetime.fromtimestamp(ts + offset, tz=timezone.utc).date().isoformat()
        pick = lambda k: _num((quote.get(k) or [None] * len(stamps))[i])  # noqa: E731
        vol = pick("volume")
        by_day[day] = {
            "symbol": symbol, "date": day, "name": name, "category": category,
            "open": pick("open"), "high": pick("high"), "low": pick("low"), "close": close,
            "prev_close": None, "change": None, "change_pct": None,
            "volume": int(vol) if vol else None, "source": "yahoo",
        }
    return with_changes([by_day[d] for d in sorted(by_day)])


def parse_treasury_csv(text):
    """財政部每日殖利率曲線 CSV → {代號: [每日一筆]}（依日期排序，含漲跌；殖利率單位是 %）"""
    out = {sym: [] for sym, _ in TREASURY_COLUMNS.values()}
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    rows = sorted(reader, key=lambda r: datetime.strptime(r["Date"], "%m/%d/%Y"))
    for r in rows:
        day = datetime.strptime(r["Date"], "%m/%d/%Y").date().isoformat()
        for col, (sym, name) in TREASURY_COLUMNS.items():
            v = _num(r.get(col))
            if v is None:
                continue
            out[sym].append({
                "symbol": sym, "date": day, "name": name, "category": "bond",
                "open": None, "high": None, "low": None, "close": v,
                "prev_close": None, "change": None, "change_pct": None, "volume": None, "source": "treasury",
            })
    return {sym: with_changes(rows) for sym, rows in out.items()}


def fetch_yahoo(symbol, name, category):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(symbol)}"
           f"?range={LOOKBACK}&interval=1d")
    return parse_yahoo(_get(url), symbol, name, category)


def fetch_treasury(today=None):
    """這個月和上個月的殖利率（月初時才有前一個交易日可以算漲跌）"""
    today = today or date.today()
    last_month = (today.replace(day=1) - timedelta(days=1))
    header, lines = None, []
    for d in (last_month, today):
        ym = f"{d.year:04d}{d.month:02d}"
        url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
               f"all/{ym}?type=daily_treasury_yield_curve&field_tdr_date_value_month={ym}&page&_format=csv")
        rows = _get(url, as_json=False).lstrip("\ufeff").strip().splitlines()
        if rows:
            header = header or rows[0]
            lines.extend(rows[1:])
        time.sleep(SLEEP_BETWEEN_API)
    return parse_treasury_csv("\n".join([header or ""] + lines))


def collect():
    """抓全部代號，回傳 (資料, 問題清單)"""
    records, problems = [], []
    for symbol, name, category in SYMBOLS:
        try:
            rows = fetch_yahoo(symbol, name, category)
            if not rows:
                problems.append(f"{symbol} {name}：沒有資料")
            records.extend(rows)
        except Exception as ex:
            problems.append(f"{symbol} {name}：下載失敗（{type(ex).__name__}）")
        time.sleep(SLEEP_BETWEEN_API)
    try:
        for sym, rows in fetch_treasury().items():
            if not rows:
                problems.append(f"{sym}：沒有資料")
            records.extend(rows)
    except Exception as ex:
        problems.append(f"美債殖利率：下載失敗（{type(ex).__name__}）")
    return records, problems


def _client():
    from supabase import create_client
    return create_client(os.getenv("ETF_SUPABASE_URL"), os.getenv("ETF_SUPABASE_KEY"))


def save(records):
    """分批 upsert，回傳 (成功筆數, 失敗筆數)"""
    client = _client()
    saved = failed = 0
    for i in range(0, len(records), 500):
        batch = records[i:i + 500]
        try:
            client.table(TABLE).upsert(batch).execute()
            saved += len(batch)
        except Exception as ex:
            failed += len(batch)
            print(f"儲存 {TABLE} 錯誤 (Index {i}): {ex}")
    return saved, failed


def main():
    records, problems = collect()
    latest = {}
    for r in records:
        latest[r["symbol"]] = r if r["date"] >= latest.get(r["symbol"], r)["date"] else latest[r["symbol"]]
    print(f"抓到 {len(records)} 筆、{len(latest)} 個代號")
    for sym, r in latest.items():
        pct = f"{r['change_pct']:+.2f}%" if r["change_pct"] is not None else "—"
        print(f"  {r['date']} {sym:9s} {r['name']}：{r['close']:,.4f}（{pct}）")
    if os.getenv("DRY_RUN") == "1":
        print("DRY_RUN=1，不寫入資料庫")
    else:
        saved, failed = save(records)
        print(f"{TABLE}：成功寫入 {saved} 筆" + (f"，{failed} 筆失敗" if failed else ""))
        if failed:
            problems.append(f"{TABLE}：{failed} 筆寫入失敗")
    for p in problems:
        print(f"❌ {p}")
    # 少數代號抓不到不算失敗（簡報會標示缺資料）；全部抓不到或寫入失敗才讓排程變紅
    return 1 if not records or any("寫入失敗" in p for p in problems) else 0


if __name__ == "__main__":
    sys.exit(main())
