"""暫時：驗證 ETF 前十大持股、費用率來源（只讀取）"""
import re
import time
import requests

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})


def get(url, **kw):
    time.sleep(3)
    r = S.get(url, timeout=60, **kw)
    r.encoding = "utf-8"
    print(f"\n### GET {url} -> {r.status_code} {len(r.content)} bytes")
    return r


def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def hidden(html):
    return {n: v for n, v in re.findall(r'<input[^>]+name="(__[A-Z]+)"[^>]+value="([^"]*)"', html)}


def tables(html, n=25):
    for t in re.findall(r"<table.*?</table>", html, re.S)[:6]:
        rows = re.findall(r"<tr.*?</tr>", t, re.S)
        if len(rows) < 3:
            continue
        print("  TABLE rows", len(rows))
        for row in rows[:n]:
            cells = [text(c).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
            print("   ", cells[:14])


# 前十大持股：IN2629，元大投信、2026-09
url = "https://www.sitca.org.tw/ROC/Industry/IN2629.aspx?pid=IN22601_04"
r = get(url)
radios = re.findall(r'<input[^>]+name="ctl00\$ContentPlaceHolder1\$rdo1"[^>]*>', r.text)
print("RADIOS", radios)
form = hidden(r.text)
form.update({"ctl00$ContentPlaceHolder1$ddlQ_YM": "202609",
             "ctl00$ContentPlaceHolder1$rdo1": "rbComid",
             "ctl00$ContentPlaceHolder1$ddlQ_Comid": "A0005",
             "ctl00$ContentPlaceHolder1$BtnQuery": "查詢"})
for rv in re.findall(r'name="ctl00\$ContentPlaceHolder1\$rdo1"[^>]*value="([^"]+)"', r.text)[:1]:
    form["ctl00$ContentPlaceHolder1$rdo1"] = rv
time.sleep(3)
p = S.post(url, data=form, timeout=60)
p.encoding = "utf-8"
print("POST", p.status_code, len(p.content))
t = text(p.text)
i = t.find("台灣50")
print("  0050 context:", t[i - 300:i + 600] if i >= 0 else "not found")
tables(p.text, 30)

# 費用率：找投信投顧公會頁面
for u in ["https://www.sitca.org.tw/ROC/Industry/IN2213.aspx?pid=IN2213_01",
          "https://www.sitca.org.tw/ROC/Industry/IN2105.aspx?pid=IN2105_01",
          "https://www.sitca.org.tw/ROC/SiteMap.aspx"]:
    try:
        r = get(u)
        t = text(r.text)
        for m in re.finditer(r"費用率", t):
            print("  費用率:", t[max(0, m.start() - 80):m.start() + 80])
            break
        for href, txt in re.findall(r'href="([^"]*IN2\d+[^"]*)"[^>]*>(.*?)</a>', r.text, re.S):
            tt = text(txt).strip()
            if re.search("費用|前十|持股|ETF|指數", tt):
                print("  LINK", tt[:50], href[:120])
    except Exception as e:
        print("ERR", e)

# 證交所 ETF e添富：找個別 ETF 資料 API
r = get("https://www.twse.com.tw/zh/ETFortune/index")
for s in sorted(set(re.findall(r'["\'](/[^"\']*(?:api|ajax|ETFortune)[^"\']*)["\']', r.text)))[:40]:
    print("  PATH", s)
for u in ["https://www.twse.com.tw/zh/ETFortune/etfInfo/0050",
          "https://www.twse.com.tw/rwd/zh/ETFortune/etfInfo?stockNo=0050&response=json",
          "https://www.twse.com.tw/zh/ETFortune/ajaxProductsResult"]:
    try:
        r = get(u)
        t = text(r.text)
        print("  ", t[:300])
        for m in re.finditer(r"費用率|經理費|前十大", t):
            print("  HIT:", t[max(0, m.start() - 100):m.start() + 200])
            break
        for s in sorted(set(re.findall(r'["\'](/[^"\']*(?:api|ajax|rwd)[^"\']*)["\']', r.text)))[:30]:
            print("  PATH", s)
    except Exception as e:
        print("ERR", e)
