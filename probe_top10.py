import etf_fetch as f, re
html = f.fetch_sitca_statement("2026-09")
names = f.parse_sitca_names(html, {})
rows = f.html_table_rows(html)
print("statement funds", len(rows)-1, "named", len(names))
page = f.http_get(f.SITCA_TOP10_URL).content.decode("utf-8","ignore")
hidden = dict(re.findall(r'<input[^>]+name="(__[A-Z]+)"[^>]+value="([^"]*)"', page))
unk=set()
for cls in f.SITCA_ETF_CLASSES:
    form=dict(hidden); form.update({"ctl00$ContentPlaceHolder1$ddlQ_YM":"202609","ctl00$ContentPlaceHolder1$rdo1":"rbClass","ctl00$ContentPlaceHolder1$ddlQ_Class":cls,"ctl00$ContentPlaceHolder1$BtnQuery":"查詢"})
    h=f.http_post(f.SITCA_TOP10_URL,data=form).content.decode("utf-8","ignore")
    _,m=f.parse_sitca_top10(h,"2026-09",names); unk|=m
for u in sorted(unk):
    k=f.fund_name_key(u); print("UNK", repr(u), "KEY", repr(k))
    pre=k[:6]
    for c in rows[1:]:
        if f.fund_name_key(c[5])[:6]==pre: print("   CAND", repr(c[2]), repr(c[5]))
print("UNKNOWN TOTAL", len(unk))
from datetime import date
m = f.fetch_margin(date(2026,10,28))
print("MARGIN rows", len(m)); print(m[:2]); print([r for r in m if r["etf_id"] in ("0050","00679B","00937B")])
