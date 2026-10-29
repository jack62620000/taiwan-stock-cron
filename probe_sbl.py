import etf_fetch as f, re
U = "https://www.sitca.org.tw/ROC/Industry/IN2211.aspx?pid=IN2222_01"
page = f.http_get(U).content.decode("utf-8", "ignore")
hidden = dict(re.findall(r'<input[^>]+name="(__[A-Z]+)"[^>]+value="([^"]*)"', page))
for y, m in [("2025", "Year"), ("2026", "9")]:
    form = dict(hidden)
    form.update({"ctl00$ContentPlaceHolder1$ddlQ_Y": y, "ctl00$ContentPlaceHolder1$ddlQ_M": m,
                 "ctl00$ContentPlaceHolder1$ddlQ_Comid": "", "ctl00$ContentPlaceHolder1$ddlQ_Fund": "",
                 "ctl00$ContentPlaceHolder1$BtnQuery": "查詢"})
    h = f.http_post(U, data=form).content.decode("utf-8", "ignore")
    trs = re.findall(r"<tr[^>]*>(.*?)</tr>", h, re.S)
    rows = [[re.sub(r"\s+", " ", re.sub(r"<[^>]+>|&nbsp;", " ", c)).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)] for tr in trs]
    rows = [r for r in rows if len(r) > 3]
    print(f"== {y} {m}: len {len(h)} rows {len(rows)}")
    for r in rows[:6]: print(r)
    etf = [r for r in rows if any("ETF" in c or "臺灣50" in c or "台灣卓越50" in c for c in r[:3])]
    print("ETF-like rows", len(etf)); [print(r) for r in etf[:5]]
