"""暫時：測試公開資訊觀測站歷史月營收頁面"""
import re
import requests

ROW = re.compile(r"<tr[^>]*>\s*<td[^>]*>\s*(\d{4,6})\s*</td>(.*?)</tr>", re.S)
CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
for host in ("mopsov.twse.com.tw", "mops.twse.com.tw"):
    for mkt in ("sii", "otc"):
        for kind in ("0", "1"):
            url = f"https://{host}/nas/t21/{mkt}/t21sc03_114_1_{kind}.html"
            try:
                r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
                r.encoding = "big5"
                rows = ROW.findall(r.text)
                print(host, mkt, kind, r.status_code, len(r.text), "rows", len(rows))
                for sid, rest in rows[:2]:
                    print("   ", sid, [re.sub(r"<[^>]+>|&nbsp;", "", c).strip() for c in CELL.findall(rest)])
            except Exception as e:
                print(host, mkt, kind, "ERR", type(e).__name__, e)
