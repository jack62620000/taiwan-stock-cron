"""暫時：找投信投顧公會「費用率」報表（只讀取）"""
import re
import time
import requests

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})


def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<script.*?</script>|<[^>]+>", " ", html, flags=re.S))


cands = [f"IN26{n:02d}" for n in range(1, 41)] + [f"IN22{n:02d}" for n in range(1, 30)] + [f"IN21{n:02d}" for n in range(1, 20)]
for code in cands:
    url = f"https://www.sitca.org.tw/ROC/Industry/{code}.aspx"
    try:
        time.sleep(1.5)
        r = S.get(url, timeout=30)
        r.encoding = "utf-8"
        if r.status_code != 200 or len(r.content) < 3000:
            continue
        t = text(r.text)
        m = re.search(r"\}\s*([^{}]{2,40}?)\s+年月", t) or re.search(r"\}\s*([^{}]{2,40}?)\s+(?:年度|季別|日期|公司)", t)
        title = m.group(1).strip() if m else t[-200:]
        print(code, len(r.content), "|", title[:60], "| 費用" if "費用" in t else "")
    except Exception as e:
        print(code, "ERR", e)
