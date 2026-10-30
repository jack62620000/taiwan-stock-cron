import etf_fetch as f, re
_, tax = f.fetch_sitca_monthly("2026-09")
by_tax = {t: c for c, t in tax.items()}
for year in (2021, 2022, 2025):
    hidden, offered = f.open_sitca_fee_year(year)
    form = dict(hidden)
    form.update({f.FEE_FORM + "ddlQ_Y": str(year), f.FEE_FORM + "ddlQ_M": "Year", f.FEE_FORM + "ddlQ_Comid": "",
                 f.FEE_FORM + "ddlQ_Fund": "", f.FEE_FORM + "BtnQuery": "查詢"})
    html = f.http_post(f.SITCA_FEE_URL, data=form, timeout=180).content.decode("utf-8", "ignore")
    trs = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
    print(year, "len", len(html), "trs", len(trs), [f.fee_cells(t)[:4] for t in trs[3:9]])
    rows = f.parse_sitca_fees(html, str(year), "year", by_tax)
    print(year, "Year rows", len(rows), [r for r in rows if r["etf_id"] == "0050"])
