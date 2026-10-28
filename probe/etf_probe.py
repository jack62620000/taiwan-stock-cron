"""
ETF 資料來源驗證（只讀取，不寫資料庫）
逐一請求 7 項資料的網址，印出狀態、欄位與 0050 / 0056 / 00878 / 00679B 的數值，供人工核對。
"""
import json, re, time, csv, io
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
      "Referer": "https://www.twse.com.tw/"}
DAY = "20261028"
TPEX_DAY = "2026%2F10%2F28"
WATCH = ["0050", "0056", "00878", "006208", "00679B", "00937B", "00400A", "00632R"]


def get(url, **kw):
    time.sleep(4)  # 避免觸發證交所 WAF
    try:
        r = requests.get(url, headers=UA, timeout=60, **kw)
        print(f"\n=== {url}\n狀態 {r.status_code}，{len(r.content)} bytes，Content-Type {r.headers.get('Content-Type')}")
        return r
    except Exception as e:
        print(f"\n=== {url}\n失敗：{e}")
        return None


def js(r):
    try:
        return r.json()
    except Exception:
        print("非 JSON，前 300 字：", r.text[:300] if r is not None else None)
        return None


def show_rows(rows, code_key, label=""):
    """rows 是 list[dict] 或 list[list]；印出 WATCH 代號的列"""
    hit = 0
    for row in rows:
        code = (row.get(code_key) if isinstance(row, dict) else row[code_key])
        code = str(code).strip()
        if code in WATCH:
            print(f"  {label}{code}: {json.dumps(row, ensure_ascii=False)[:600]}")
            hit += 1
    etf = [r for r in rows if str(r.get(code_key) if isinstance(r, dict) else r[code_key]).strip().startswith("00")]
    print(f"  共 {len(rows)} 列，00 開頭 {len(etf)} 列，觀察清單命中 {hit}")


def twse_rwd_tables(d):
    """證交所 rwd 回傳可能是 tables[] 或 fields/data"""
    if d is None:
        return
    print("  stat:", d.get("stat"), "date:", d.get("date"))
    tables = d.get("tables") or [{"title": d.get("title"), "fields": d.get("fields"), "data": d.get("data")}]
    for t in tables:
        if not t.get("data"):
            continue
        print(f"  表格「{t.get('title')}」欄位：{t.get('fields')}")
        show_rows(t["data"], 0)


# ---------- 1. ETF 清單 ----------
print("\n########## 1. ETF 清單與基本資料")
r = get("https://openapi.twse.com.tw/v1/opendata/t187ap47_L")
d = js(r)
if d:
    print("  欄位：", list(d[0].keys()))
    show_rows(d, "基金代號")
    types = {}
    for x in d:
        types[x.get("基金類型")] = types.get(x.get("基金類型"), 0) + 1
    print("  基金類型分布：", json.dumps(types, ensure_ascii=False))

r = get("https://www.tpex.org.tw/openapi/swagger.json")
d = js(r)
if d:
    for p, v in d.get("paths", {}).items():
        s = json.dumps(v, ensure_ascii=False)
        summ = list(v.values())[0].get("summary", "")
        if re.search(r"ETF|ETN|指數股票|受益|基金|除權|債券", summ):
            print("  櫃買 OpenAPI：", p, summ)

# ---------- 2. 每日價量 ----------
print("\n########## 2. 每日價量")
twse_rwd_tables(js(get(f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={DAY}&type=0099P&response=json")))
twse_rwd_tables(js(get(f"https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date={DAY}&stockNo=0050&response=json")))
r = get(f"https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={TPEX_DAY}&type=AL&id=&response=json&order=0&sort=asc")
d = js(r)
if d:
    for t in d.get("tables", []):
        print(f"  櫃買表格「{t.get('title')}」欄位：{t.get('fields')}，日期 {t.get('date')}")
        if t.get("data"):
            show_rows(t["data"], 0)
r = get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes")
d = js(r)
if d:
    print("  欄位：", list(d[0].keys()))
    show_rows(d, "SecuritiesCompanyCode")

# ---------- 3. 淨值、折溢價 ----------
print("\n########## 3. MIS 淨值折溢價")
r = get("https://mis.twse.com.tw/stock/data/all_etf.txt", )
d = js(r)
if d:
    rows = []
    for blk in d.get("a1", []):
        for m in blk.get("msgArray", []) or []:
            rows.append(m)
    print("  頂層鍵：", list(d.keys()), "筆數：", len(rows))
    show_rows(rows, "a")

# ---------- 4. 配息 ----------
print("\n########## 4. 配息")
d = js(get("https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL"))
if d:
    print("  欄位：", list(d[0].keys()))
    show_rows(d, "Code")
d = js(get("https://www.twse.com.tw/rwd/zh/ETF/etfDiv?response=json"))
if d:
    print("  stat:", d.get("stat"), "fields:", d.get("fields"))
    show_rows(d.get("data", []), 0)
d = js(get("https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost"))
if d:
    print("  欄位：", list(d[0].keys()) if d else None)
    show_rows(d, list(d[0].keys())[1] if d else 0)
    print("  前 2 列：", json.dumps(d[:2], ensure_ascii=False)[:600])

# ---------- 5. 三大法人 ----------
print("\n########## 5. 三大法人")
twse_rwd_tables(js(get(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={DAY}&selectType=0099P&response=json")))
d = js(get(f"https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date={TPEX_DAY}&id=&response=json"))
if d:
    for t in d.get("tables", []):
        print(f"  櫃買法人表格「{t.get('title')}」欄位數 {len(t.get('fields') or [])}")
        if t.get("data"):
            show_rows(t["data"], 0)

# ---------- 6. SITCA 每月 ----------
print("\n########## 6. 投信公會每月統計")
r = get("https://www.sitca.org.tw/ROC/SITCA_ETF/etf_statement2.aspx?txtYM=202609&txtemcode=")
if r is not None:
    html = r.content.decode("utf-8", "ignore")
    trs = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
    rows = []
    for tr in trs:
        cells = [re.sub(r"<[^>]+>|&nbsp;", "", c).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        if len(cells) >= 20:
            rows.append(cells)
    print("  表格列數：", len(rows))
    if rows:
        print("  表頭：", rows[0])
        for c in rows[1:]:
            if len(c) > 2 and c[2] in WATCH:
                print("  ", c)
        print("  00 開頭列數：", sum(1 for c in rows if len(c) > 2 and c[2].startswith("00")))

# ---------- 7. 集保 ----------
print("\n########## 7. 集保持股分級")
r = get("https://smart.tdcc.com.tw/opendata/getOD.ashx?id=1-5", verify=False)
if r is not None:
    text = r.content.decode("utf-8-sig", "ignore")
    rows = list(csv.reader(io.StringIO(text)))
    print("  表頭：", rows[0], "總列數：", len(rows))
    codes = {row[1].strip() for row in rows[1:] if len(row) > 1}
    etf = sorted(c for c in codes if c.startswith("00"))
    print(f"  00 開頭代號 {len(etf)} 檔，含字尾：", [c for c in etf if not c.isdigit()][:20])
    for row in rows[1:]:
        if len(row) > 1 and row[1].strip() == "0050":
            print("  0050:", row)
print("\n完成")
