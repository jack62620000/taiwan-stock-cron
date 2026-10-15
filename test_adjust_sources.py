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
    # ---- 第二輪：找櫃買中心「變更面額」的網址，以及 FinMind 不指定股票的查法 ----
    ("櫃買 變更面額 parvaluechg",
     f"https://www.tpex.org.tw/www/zh-tw/bulletin/parvaluechg?startDate={slash(START)}&endDate={slash(END)}&response=json"),
    ("櫃買 變更面額 parValueChg",
     f"https://www.tpex.org.tw/www/zh-tw/bulletin/parValueChg?startDate={slash(START)}&endDate={slash(END)}&response=json"),
    ("櫃買 變更面額 chgParValue",
     f"https://www.tpex.org.tw/www/zh-tw/bulletin/chgParValue?startDate={slash(START)}&endDate={slash(END)}&response=json"),
    ("櫃買 變更面額 pvchg",
     f"https://www.tpex.org.tw/www/zh-tw/bulletin/pvchg?startDate={slash(START)}&endDate={slash(END)}&response=json"),
    ("櫃買 變更面額 revivtPar",
     f"https://www.tpex.org.tw/www/zh-tw/bulletin/revivtPar?startDate={slash(START)}&endDate={slash(END)}&response=json"),
    ("櫃買 變更面額 舊網址 parvalue",
     f"https://www.tpex.org.tw/web/stock/exright/parvalue/parvalue_result.php?l=zh-tw&d={roc(START)}&ed={roc(END)}"),
    ("櫃買 OpenAPI 變更面額",
     "https://www.tpex.org.tw/openapi/v1/tpex_parvalue_change"),
    ("櫃買 OpenAPI 減資",
     "https://www.tpex.org.tw/openapi/v1/tpex_capital_reduction"),
    ("FinMind 變更面額（不指定股票）",
     f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockParValueChange&start_date={START}&end_date={END}"),
    ("FinMind 分割（不指定股票）",
     f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockSplitPrice&start_date={START}&end_date={END}"),
    ("FinMind 減資（不指定股票）",
     f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockCapitalReductionReferencePrice&start_date={START}&end_date={END}"),
    ("FinMind 除權息結果 5904",
     f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockDividendResult&data_id=5904&start_date={START}&end_date={END}"),
    ("FinMind 除權息結果 4747",
     f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockDividendResult&data_id=4747&start_date={START}&end_date={END}"),
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
