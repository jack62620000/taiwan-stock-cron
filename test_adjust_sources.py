"""
還原權值資料來源測試（只抓資料、印出結果，不寫入資料庫）

逐一試抓證交所、櫃買中心、FinMind 的除權息、減資、變更面額（分割）資料，
印出每個網址抓不抓得到、欄位名稱、筆數、前幾筆內容，
並檢查資料庫裡已知的減資、分割案例有沒有出現在結果中。
"""
import json
import time

import requests
import urllib3

urllib3.disable_warnings()

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# 測試期間：2026/8～9 月（資料庫裡有多起減資、分割造成的大幅跳空）
START, END = "2026-08-01", "2026-09-30"

# 資料庫裡觀察到的跳空案例（股票、日期），用來核對來源有沒有這些事件
KNOWN = {
    "6669": "2026-09-02 上市 7800→2610",
    "6949": "2026-09-07 上市 1490→67.1",
    "6550": "2026-09-29 上市 9.92→17.4",
    "2321": "2026-09-21 上市 15.1→26.0",
    "5904": "2026-08-10 上櫃 720→79.2",
    "5314": "2026-08-14 上櫃 61.3→16.2",
    "4747": "2026-08-31 上櫃 56.5→27.35",
    "8277": "2026-09-21 上櫃 8.05→16.85",
}


def ymd(d):
    return d.replace("-", "")


def roc(d, sep="/"):
    y, m, dd = d.split("-")
    return f"{int(y) - 1911}{sep}{m}{sep}{dd}"


def slash(d):
    return d.replace("-", "/")


SOURCES = [
    # ---- 證交所（上市）----
    ("證交所 除權除息計算結果表 rwd",
     f"https://www.twse.com.tw/rwd/zh/exRight/TWT49U?startDate={ymd(START)}&endDate={ymd(END)}&response=json"),
    ("證交所 除權除息計算結果表 舊網址",
     f"https://www.twse.com.tw/exchangeReport/TWT49U?response=json&strDate={ymd(START)}&endDate={ymd(END)}"),
    ("證交所 減資恢復買賣參考價格 rwd",
     f"https://www.twse.com.tw/rwd/zh/reducation/TWTAUU?startDate={ymd(START)}&endDate={ymd(END)}&response=json"),
    ("證交所 減資恢復買賣參考價格 舊網址",
     f"https://www.twse.com.tw/exchangeReport/TWTAUU?response=json&strDate={ymd(START)}&endDate={ymd(END)}"),
    ("證交所 變更面額恢復買賣參考價格 rwd",
     f"https://www.twse.com.tw/rwd/zh/change/TWTB8U?startDate={ymd(START)}&endDate={ymd(END)}&response=json"),
    ("證交所 變更面額恢復買賣參考價格 舊網址",
     f"https://www.twse.com.tw/exchangeReport/TWTB8U?response=json&strDate={ymd(START)}&endDate={ymd(END)}"),
    # ---- 櫃買中心（上櫃）----
    ("櫃買 除權除息計算結果 舊網址",
     f"https://www.tpex.org.tw/web/stock/exright/dailyquo/exDailyQ_result.php?l=zh-tw&d={roc(START)}&ed={roc(END)}"),
    ("櫃買 除權除息計算結果 新網址",
     f"https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?startDate={slash(START)}&endDate={slash(END)}&response=json"),
    ("櫃買 減資恢復買賣參考價 舊網址",
     f"https://www.tpex.org.tw/web/stock/exright/revivt/revivt_result.php?l=zh-tw&d={roc(START)}&ed={roc(END)}"),
    ("櫃買 減資恢復買賣參考價 新網址",
     f"https://www.tpex.org.tw/www/zh-tw/bulletin/revivt?startDate={slash(START)}&endDate={slash(END)}&response=json"),
    ("櫃買 變更面額恢復買賣參考價 舊網址",
     f"https://www.tpex.org.tw/web/stock/exright/parvaluechg/parvaluechg_result.php?l=zh-tw&d={roc(START)}&ed={roc(END)}"),
    ("櫃買 變更面額恢復買賣參考價 新網址",
     f"https://www.tpex.org.tw/www/zh-tw/bulletin/parValueChange?startDate={slash(START)}&endDate={slash(END)}&response=json"),
    # ---- 櫃買 OpenAPI（只有最新資料，看欄位用）----
    ("櫃買 OpenAPI 除權除息",
     "https://www.tpex.org.tw/openapi/v1/tpex_exright_daily"),
    # ---- FinMind（備援，免費版有次數限制）----
    ("FinMind 除權息結果 6669",
     f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockDividendResult&data_id=6669&start_date={START}&end_date={END}"),
    ("FinMind 減資參考價 5314",
     f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockCapitalReductionReferencePrice&data_id=5314&start_date={START}&end_date={END}"),
    ("FinMind 分割參考價 6669",
     f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockSplitPrice&data_id=6669&start_date={START}&end_date={END}"),
    ("FinMind 變更面額 6949",
     f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockParValueChange&data_id=6949&start_date={START}&end_date={END}"),
]


def find_rows(obj):
    """在 JSON 裡找出最長的資料列清單，回傳 (路徑, 欄位, 資料列)"""
    best = ("", None, [])

    def walk(o, path):
        nonlocal best
        if isinstance(o, dict):
            fields = o.get("fields")
            for k, v in o.items():
                if isinstance(v, list) and v and isinstance(v[0], (list, dict)) and len(v) > len(best[2]):
                    best = (f"{path}.{k}", fields, v)
                walk(v, f"{path}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o[:5]):
                walk(v, f"{path}[{i}]")

    if isinstance(obj, list) and obj and isinstance(obj[0], (list, dict)):
        best = ("", None, obj)
    walk(obj, "")
    return best


def test(name, url):
    print("=" * 100)
    print(f"【{name}】\n{url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=30, verify=False)
    except Exception as e:
        print(f"  ❌ 連線失敗：{type(e).__name__}: {e}")
        return
    print(f"  HTTP {r.status_code}，{r.headers.get('content-type', '')}，{len(r.content):,} bytes")
    try:
        data = r.json()
    except Exception:
        print(f"  ⚠️ 不是 JSON，前 300 字：{r.text[:300]!r}")
        return
    if isinstance(data, dict):
        print(f"  最上層欄位：{list(data.keys())[:15]}")
        for k in ("stat", "status", "msg", "title", "date", "reportDate"):
            if k in data:
                print(f"  {k} = {str(data[k])[:120]}")
    path, fields, rows = find_rows(data)
    print(f"  資料列：{len(rows)} 筆（位置 {path or '最上層'}）")
    if fields:
        print(f"  欄位：{fields}")
    for row in rows[:3]:
        print(f"    {json.dumps(row, ensure_ascii=False)[:300]}")
    text = json.dumps(rows, ensure_ascii=False)
    hits = [f"{sid}（{desc}）" for sid, desc in KNOWN.items() if f'"{sid}"' in text or f"'{sid}'" in text or f'"{sid} ' in text]
    print(f"  已知案例出現：{hits if hits else '無'}")


def main():
    print(f"測試期間 {START} ～ {END}，不寫入資料庫\n")
    for name, url in SOURCES:
        test(name, url)
        time.sleep(3)  # 放慢請求節奏，避免被來源封鎖
    print("\n✅ 測試結束")


if __name__ == "__main__":
    main()
