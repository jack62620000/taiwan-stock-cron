import etf_fetch as f, json, re
def show(name, url, n=600):
    try:
        r = f.http_get(url, retries=1)
        t = r.content.decode("utf-8", "ignore")
        print(f"== {name} {r.status_code} len={len(t)}"); print(t[:n].replace("\n"," "))
        return t
    except Exception as e:
        print(f"== {name} FAIL {e}")
t = show("TWT93U", "https://www.twse.com.tw/exchangeReport/TWT93U?response=json&date=20261028", 300)
if t:
    d = json.loads(t); print("keys", list(d.keys()))
    for k in ("fields","tables"):
        if k in d: print(k, json.dumps(d[k] if k=="fields" else [{"title":x.get("title"),"fields":x.get("fields"),"n":len(x.get("data",[]))} for x in d[k]], ensure_ascii=False)[:1500])
    rows = d.get("data") or (d.get("tables") or [{}])[0].get("data", [])
    print("rows", len(rows), [r for r in rows if str(r[0]).strip() in ("0050","00679B","00878")][:3])
for name, url in [
    ("TPEx sbl www", "https://www.tpex.org.tw/www/zh-tw/margin/sbl?date=2026%2F10%2F28&id=&response=json"),
    ("TPEx sbl2", "https://www.tpex.org.tw/www/zh-tw/margin/sbl?date=2026/10/28&response=json"),
    ("TPEx old", "https://www.tpex.org.tw/web/stock/margin_trading/margin_sbl/margin_sbl_result.php?l=zh-tw&d=115/10/28&o=json"),
    ("TPEx openapi", "https://www.tpex.org.tw/openapi/v1/tpex_margin_sbl"),
    ("TWSE openapi TWT93U", "https://openapi.twse.com.tw/v1/exchangeReport/TWT93U"),
    ("SITCA etf_info", "https://www.sitca.org.tw/ROC/SITCA_ETF/etf_info.aspx"),
]:
    t = show(name, url, 400)
    if t and name.startswith("TPEx") and t.strip().startswith("{"):
        d = json.loads(t); print("keys", list(d.keys()))
        for tb in d.get("tables", []): print(tb.get("title"), tb.get("fields"), len(tb.get("data",[])), [r for r in tb.get("data",[]) if str(r[0]).strip() in ("00679B","00937B")][:2])
    if t and name == "SITCA etf_info":
        print("費用 hits:", [m.start() for m in re.finditer("費用", t)][:10]); print(re.sub(r"<[^>]+>"," ",t)[:1500])
