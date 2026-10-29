"""暫時：驗證 ETF 融資融券、前十大持股、費用率的來源（只讀取）"""
import re
import time
import requests

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
S = requests.Session()
S.headers.update(H)


def get(url, **kw):
    time.sleep(4)
    r = S.get(url, timeout=60, **kw)
    print(f"\n### GET {url} -> {r.status_code} {len(r.content)} bytes")
    return r


def links(html, pat):
    for href, txt in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        t = re.sub(r"<[^>]+>|\s+", " ", txt).strip()
        if re.search(pat, t + href):
            print("  LINK", t[:60], "|", href[:150])


def forms(html):
    for name in re.findall(r'<(?:select|input)[^>]+name="([^"]+)"', html)[:60]:
        print("  FIELD", name)
    for sel in re.findall(r'<select[^>]+name="([^"]+)"[^>]*>(.*?)</select>', html, re.S):
        opts = re.findall(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)', sel[1])
        print("  SELECT", sel[0], len(opts), opts[:8])


# 1. 融資融券
r = get("https://www.twse.com.tw/exchangeReport/MI_MARGN?response=json&date=20261028&selectType=ALL")
d = r.json()
for t in d.get("tables", []):
    print("TABLE", t.get("title"), t.get("fields"))
    rows = [x for x in t.get("data", []) if str(x[0]).startswith("00")]
    print("  ETF rows", len(rows), rows[:3])
r = get("https://www.twse.com.tw/exchangeReport/MI_MARGN?response=json&date=20261028&selectType=0099P")
d = r.json()
print(d.get("stat"), [(t.get("title"), len(t.get("data", []))) for t in d.get("tables", [])])
r = get("https://www.tpex.org.tw/www/zh-tw/margin/balance?date=2026%2F10%2F28&id=&response=json")
d = r.json()
for t in d.get("tables", []):
    print("TABLE", t.get("title"), t.get("fields"))
    rows = [x for x in t.get("data", []) if str(x[0]).startswith("00")]
    print("  ETF rows", len(rows), rows[:3])

# 2、3. 投信投顧公會：找前十大持股、費用率
for url in ["https://www.sitca.org.tw/ROC/Industry/IN2629.aspx?pid=IN22601_04",
            "https://www.sitca.org.tw/ROC/Industry/IN2630.aspx?pid=IN22601_05",
            "https://www.sitca.org.tw/ROC/SITCA_ETF/etf_statement2.aspx"]:
    try:
        r = get(url)
        r.encoding = "utf-8"
        html = r.text
        print("  TITLE", re.findall(r"<title>(.*?)</title>", html, re.S)[:1])
        forms(html)
        links(html, r"費用|前十|持股|IN2|ETF")
    except Exception as e:
        print("ERR", e)

# MOPS ETF 相關
for url in ["https://mops.twse.com.tw/mops/web/t78sb39_q3",
            "https://www.twse.com.tw/zh/ETFortune/index"]:
    try:
        r = get(url)
        r.encoding = "utf-8"
        print("  TITLE", re.findall(r"<title>(.*?)</title>", r.text, re.S)[:1])
        links(r.text, r"費用|前十|持股")
    except Exception as e:
        print("ERR", e)
