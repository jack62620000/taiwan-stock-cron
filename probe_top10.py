import etf_fetch as f
for ym in ["2023-10", "2025-03"]:
    html = f.fetch_sitca_statement(ym)
    names = f.parse_sitca_names(html, {})
    rows = f.fetch_sitca_top10(ym, names)
    ids = sorted({r["etf_id"] for r in rows})
    print(ym, "named", len(names), "rows", len(rows), "etfs", len(ids), ids[:5], [r for r in rows if r["etf_id"]=="0050"][:2])
