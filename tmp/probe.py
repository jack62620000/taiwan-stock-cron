import re, requests
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
TAG = re.compile(r"<[^>]+>|&nbsp;")
def cells(tr): return [TAG.sub("", c).strip() for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)]
for typek, y, s, sid in (("sii", 114, "02", "2330"), ("otc", 113, "01", "5347"), ("sii", 115, "02", "2330")):
    data = {"encodeURIComponent": 1, "step": 1, "firstin": 1, "off": 1, "isQuery": "Y", "TYPEK": typek, "year": y, "season": s}
    r = requests.post("https://mopsov.twse.com.tw/mops/web/ajax_t163sb20", data=data, headers=H, timeout=90); r.encoding = "utf-8"
    print("=====", typek, y, s, len(r.text))
    rows = [cells(tr) for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S)]
    heads = [c for c in rows if c and c[0] == "公司代號"]
    print("header kinds", len({tuple(h) for h in heads}))
    for h in {tuple(h) for h in heads}: print("  H", list(h))
    print("  data rows", sum(1 for c in rows if c and re.fullmatch(r"\d{4}", c[0])))
    for c in rows:
        if c and c[0] == sid: print("  S", c)
    print(TAG.sub(" ", r.text[:1500]).split()[:40])
