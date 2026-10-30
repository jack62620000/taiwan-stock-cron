"""暫時用：在 GitHub Actions 上確認官方開放資料／公開資訊觀測站的 ETF 欄位格式（開 PR 前會刪掉）"""
import json, re, time, requests

def get(url, method="GET", **kw):
    time.sleep(3)
    try:
        r = requests.request(method, url, timeout=30, **kw)
        r.encoding = "utf-8"
        print(f"\n### {method} {url} {kw.get('json', kw.get('data', ''))} -> {r.status_code} {r.headers.get('content-type')} {len(r.content)}B")
        return r
    except Exception as e:
        print(f"\n### {method} {url} -> ERR {e}")

def ctx(text, pat, n=150, limit=12):
    for i, m in enumerate(re.finditer(pat, text)):
        if i >= limit:
            break
        print("  >", text[max(0, m.start() - n): m.end() + n].replace("\n", " "))

# 1. 開放資料：暫停交易、ETF、基金相關 API
for sw in ("https://openapi.twse.com.tw/v1/swagger.json", "https://www.tpex.org.tw/openapi/swagger.json"):
    r = get(sw)
    if r is not None and r.ok:
        for p, v in r.json().get("paths", {}).items():
            summ = next(iter(v.values())).get("summary", "")
            if re.search(r"暫停|停止|恢復|ETF|基金|受益|收益分配", summ + p):
                print(" ", p, summ)

for u in ("https://openapi.twse.com.tw/v1/announcement/TWTAWU",):
    r = get(u)
    if r is not None and r.ok:
        print(r.text[:600])

# 2. 公開資訊觀測站新版：前端怎麼呼叫 API、ETF 基本資料與收益分配頁
js = get("https://mops.twse.com.tw/mops/assets/index.js")
if js is not None and js.ok:
    t = js.text
    ctx(t, r"/mops/api", 120, 15)
    ctx(t, r"t51sb11[\"'`]", 200, 5)
    ctx(t, r"收益分配|分配收益|收益平準金|配息組成", 150, 15)
