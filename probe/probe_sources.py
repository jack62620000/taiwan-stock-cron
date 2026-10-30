"""暫時用：在 GitHub Actions 上找 ETF 配息組成、分割預告的資料來源（開 PR 前會刪掉）"""
import re, requests
H = {"User-Agent": "Mozilla/5.0"}
B = "https://www.twse.com.tw"

def get(url, method="GET", **kw):
    try:
        r = requests.request(method, url, headers=H, timeout=30, **kw)
        print(f"\n### {method} {url} {kw.get('data','')} -> {r.status_code} {r.headers.get('content-type')} {len(r.content)}B")
        r.encoding = "utf-8"
        return r
    except Exception as e:
        print(f"\n### {method} {url} -> ERR {e}")

def scripts(html):
    for s in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S):
        if "ajax" in s or "$.post" in s or "$.get" in s or "url" in s:
            print("  SCRIPT:", re.sub(r"\s+", " ", s)[:1500])

def text(html, n=2500):
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    print("  TEXT:", re.sub(r"\s+", " ", t)[:n])

for path in ("/zh/ETFortune/split", "/zh/ETFortune/dividendList", "/zh/ETFortune/dividendCalendar"):
    r = get(B + path)
    if r is not None and r.ok:
        scripts(r.text)
        i = r.text.find("<table")
        if i >= 0:
            text(r.text[i:i + 20000])

for code in ("0056", "00878"):
    r = get(f"{B}/zh/ETFortune/etfInfo/{code}")
    if r is not None and r.ok:
        scripts(r.text)
        for m in re.finditer(r"收益|平準金|配息組成|分配組成|資本利得", r.text):
            print("  CTX:", re.sub(r"\s+|<[^>]+>", " ", r.text[max(0, m.start()-200): m.end()+300]))
            break

r = get(B + "/zh/ETFortune/ajaxDividendData", "POST", data={"type": "top"})
if r is not None:
    print(r.text[:2000])
