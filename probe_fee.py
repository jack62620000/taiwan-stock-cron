import etf_fetch as f
_, tax = f.fetch_sitca_monthly("2026-09")
by_tax = {t: c for c, t in tax.items()}
for year in (2021, 2026):
    hidden, offered = f.open_sitca_fee_year(year)
    print(year, "offered", offered)
    for mv in [offered[1], offered[-1]] if year == 2026 else [offered[0], offered[-1]]:
        rows = f.fetch_sitca_fees(year, mv, by_tax, hidden)
        print(mv, len(rows), [r for r in rows if r["etf_id"] in ("0050", "00679B", "00981A")])
