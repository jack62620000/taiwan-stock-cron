"""暫時用：在 GitHub Actions 上找 ETF 配息組成、PCF、指數的資料來源（開 PR 前會刪掉）"""
import json, re, requests
H = {"User-Agent": "Mozilla/5.0"}
KW = re.compile(r"ETF|收益分配|配息|PCF|申購買回|指數|index", re.I)

def get(url, **kw):
    try:
        r = requests.get(url, headers=H, timeout=30, **kw)
        print(f"\n### GET {url} -> {r.status_code} {r.headers.get('content-type')} {len(r.content)}B")
        return r
    except Exception as e:
        print(f"\n### GET {url} -> ERR {e}")

for sw in ("https://openapi.twse.com.tw/v1/swagger.json", "https://www.tpex.org.tw/openapi/swagger.json"):
    r = get(sw)
    if r is not None and r.ok:
        try:
            paths = r.json().get("paths", {})
            for p, v in paths.items():
                s = json.dumps(v, ensure_ascii=False)
                if KW.search(p) or KW.search(s[:400]):
                    summ = next(iter(v.values())).get("summary", "")
                    print(" ", p, summ)
        except Exception as e:
            print("parse err", e)

# 新版公開資訊觀測站：從前端程式找頁面代號
r = get("https://mops.twse.com.tw/mops/")
if r is not None and r.ok:
    js = re.findall(r'src="([^"]+\.js)"', r.text)
    print("js:", js)
    for j in js:
        u = j if j.startswith("http") else "https://mops.twse.com.tw" + (j if j.startswith("/") else "/mops/" + j)
        jr = get(u)
        if jr is None or not jr.ok:
            continue
        for m in set(re.findall(r'.{0,80}(?:收益分配|ETF).{0,80}', jr.text)):
            print("  JS:", m)
for u in ("https://mops.twse.com.tw/mops/api/menu", "https://mops.twse.com.tw/mops/web/t05st01",
          "https://mopsov.twse.com.tw/mops/web/index"):
    r = get(u)
    if r is not None:
        print(r.text[:1500])
