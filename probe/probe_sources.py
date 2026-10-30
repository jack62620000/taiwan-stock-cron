"""暫時用：在 GitHub Actions 上找 ETF 配息組成、分割公告、PCF 的資料來源（開 PR 前會刪掉）"""
import re, requests
H = {"User-Agent": "Mozilla/5.0"}

def get(url, method="GET", **kw):
    try:
        r = requests.request(method, url, headers=H, timeout=30, **kw)
        print(f"\n### {method} {url} -> {r.status_code} {r.headers.get('content-type')} {len(r.content)}B")
        return r
    except Exception as e:
        print(f"\n### {method} {url} -> ERR {e}")

def grep(text, pat, n=60, limit=40):
    seen = set()
    for m in re.finditer(pat, text):
        s = text[max(0, m.start() - n): m.end() + n].replace("\n", " ")
        if s not in seen:
            seen.add(s); print("  >", s)
        if len(seen) >= limit:
            break

# 1. 公開資訊觀測站：配息、分割相關頁面與 API 寫法
js = get("https://mops.twse.com.tw/mops/assets/index.js")
if js is not None and js.ok:
    t = js.text
    print("== 收益/配息/分割/除息 =="); grep(t, r"收益|配息|分割|除息|收益分配")
    print("== api 呼叫 =="); grep(t, r"/api/|baseURL|apiUrl|axios\.create", 80, 15)
    print("== ETF 頁代號 =="); grep(t, r"t78sb\d+|o_t78sb\d+", 50, 40)

# 2. 證交所 ETF e添富、ETF 專區
for u in ("https://www.twse.com.tw/zh/ETFortune/index", "https://www.twse.com.tw/zh/products/securities/etf/news.html",
          "https://www.twse.com.tw/zh/ETFortune/dividendCalendar"):
    r = get(u)
    if r is not None and r.ok:
        grep(r.text, r"rwd/[^\"' ]+|ETFortune/[A-Za-z/]+|\.js\"", 30, 30)
        for j in re.findall(r'src="([^"]+\.js)"', r.text)[:8]:
            jr = get(j if j.startswith("http") else "https://www.twse.com.tw" + j)
            if jr is not None and jr.ok:
                grep(jr.text, r"rwd/[A-Za-z0-9_/]+|ETFortune[A-Za-z0-9_/]*", 20, 30)

# 3. 證交所暫停交易／分割公告候選
for u in ("https://www.twse.com.tw/rwd/zh/announcement/TWTAWU?response=json",
          "https://www.twse.com.tw/rwd/zh/announcement/notetrans?response=json",
          "https://www.twse.com.tw/rwd/zh/ETF/etfDiv?response=json",
          "https://openapi.twse.com.tw/v1/announcement/TWTAWU",
          "https://openapi.twse.com.tw/v1/exchangeReport/TWT85U"):
    r = get(u)
    if r is not None:
        print(r.text[:800])
