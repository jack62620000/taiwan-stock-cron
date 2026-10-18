import re, requests
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
TAG = re.compile(r"<[^>]+>|&nbsp;")
def post(report, extra):
    url = f"https://mopsov.twse.com.tw/mops/web/ajax_{report}"
    data = {"encodeURIComponent": 1, "step": 1, "firstin": 1, "off": 1, "isQuery": "Y", **extra}
    r = requests.post(url, data=data, headers=H, timeout=90); r.encoding = "utf-8"
    t = r.text
    print("=====", report, extra, r.status_code, len(t))
    heads = re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S)
    print("rows", len(heads))
    for h in heads[:4]:
        print("  ", [TAG.sub("", c).strip() for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", h, re.S)][:20])
    if len(heads) == 0:
        print(TAG.sub(" ", t)[:400])
base = {"TYPEK": "sii", "year": 114, "season": "02"}
for rep in ["t163sb20", "t163sb06", "t163sb07", "t163sb08", "t163sb09"]:
    try: post(rep, base)
    except Exception as e: print(rep, "ERR", e)
try: post("t164sb05", {"TYPEK": "all", "co_id": "2330", "year": 114, "season": "02"})
except Exception as e: print("t164sb05 ERR", e)
