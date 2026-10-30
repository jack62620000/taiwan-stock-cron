import etf_fetch as f, re
U = "https://www.sitca.org.tw/ROC/Industry/IN2211.aspx?pid=IN2222_01"
_, tax = f.fetch_sitca_monthly("2026-09")
by_tax = {t: c for c, t in tax.items()}
print("tax ids", len(by_tax))
page = f.http_get(U).content.decode("utf-8", "ignore")
hidden = dict(re.findall(r'<input[^>]+name="(__[A-Z]+)"[^>]+value="([^"]*)"', page))
for sel in re.findall(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', page, re.S):
    print(sel[0], re.findall(r'value="([^"]*)"[^>]*>([^<]*)', sel[1])[:20])
for y, m in [("2026", "9"), ("2026", "0630"), ("2021", "1"), ("2021", "Year")]:
    form = dict(hidden)
    form.update({"ctl00$ContentPlaceHolder1$ddlQ_Y": y, "ctl00$ContentPlaceHolder1$ddlQ_M": m,
                 "ctl00$ContentPlaceHolder1$ddlQ_Comid": "", "ctl00$ContentPlaceHolder1$ddlQ_Fund": "",
                 "ctl00$ContentPlaceHolder1$BtnQuery": "查詢"})
    h = f.http_post(U, data=form).content.decode("utf-8", "ignore")
    trs = re.findall(r"<tr[^>]*>(.*?)</tr>", h, re.S)
    rows = [[re.sub(r"\s+", " ", re.sub(r"<[^>]+>|&nbsp;", " ", c)).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)] for tr in trs]
    print(f"== {y} {m}: len {len(h)} trs {len(rows)}")
    lens = {}
    for r in rows: lens[len(r)] = lens.get(len(r), 0) + 1
    print("cell counts", lens)
    for i, r in enumerate(rows):
        if not re.fullmatch(r"\d{8}", r[1] if len(r) > 1 else ""):
            print("hdr", i, r[:30])
    d = [r for r in rows if len(r) > 1 and re.fullmatch(r"\d{8}", r[1])]
    print("first", d[:2])
    hit = [r for r in d if r[1] in by_tax]
    print("etf matched", len(hit), "of", len(d)); [print(by_tax[r[1]], r) for r in hit[:3]]
    print("selected", re.findall(r'<option selected="selected" value="([^"]*)"', h))
