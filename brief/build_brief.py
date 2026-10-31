"""
每日台股早報：把一份 JSON 資料畫成長圖（PNG）和 PDF。

用法：python3 build_brief.py <data.json> <輸出資料夾>
輸出：<輸出資料夾>/台股早報-<日期>.png、.pdf（同一份 HTML 也會留下，方便檢查）

顏色照台股習慣：上漲紅（#dc2626）、下跌綠（#16a34a）。字型用系統的 WenQuanYi Zen Hei（支援繁體中文）。
week_ahead（未來一週：date、event、why）可省略。JSON 欄位見各日期資料夾的 data.json；數字一律由呼叫端算好，這支程式只負責排版，不做任何推論。
"""
import html
import json
import sys
from pathlib import Path

UP, DOWN, FLAT = "#dc2626", "#16a34a", "#94a3b8"


def esc(v):
    return html.escape(str(v)) if v is not None else "—"


def sign_color(v):
    if v is None:
        return FLAT
    return UP if v > 0 else DOWN if v < 0 else FLAT


def pct(v, digits=2):
    return "—" if v is None else f"{v:+.{digits}f}%"


def num(v, digits=2):
    return "—" if v is None else f"{v:,.{digits}f}"


def quote_cards(items, cols):
    cells = []
    for q in items:
        c = sign_color(q.get("chg_pct"))
        sub = f'<div class="qsub">{esc(q["sub"])}</div>' if q.get("sub") else ""
        cells.append(
            f'<div class="q"><div class="qname">{esc(q["name"])}</div>{sub}'
            f'<div class="qval">{num(q.get("close"), q.get("digits", 2))}</div>'
            f'<div class="qpct" style="color:{c}">{pct(q.get("chg_pct"))}</div></div>')
    return f'<div class="grid" style="grid-template-columns:repeat({cols},1fr)">{"".join(cells)}</div>'


def rates_table(rows):
    out = []
    for r in rows:
        c = sign_color(r.get("dir"))
        out.append(f'<tr><td>{esc(r["name"])}</td><td class="r">{esc(r["value"])}</td>'
                   f'<td class="r" style="color:{c}">{esc(r.get("change", ""))}</td></tr>')
    return f'<table class="mini">{"".join(out)}</table>'


def sparkline(sp, w=430, h=70):
    """sp：{"closes": [近 20 日收盤], "ma20": [同長度 20 日線，可含 null], "low": 前低價位}；畫收盤線、20 日線、前低虛線"""
    closes = [c for c in sp.get("closes", []) if c is not None]
    if len(closes) < 2:
        return ""
    ma = sp.get("ma20") or []
    vals = closes + [v for v in ma if v is not None] + ([sp["low"]] if sp.get("low") else [])
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    n = len(sp["closes"])
    xy = lambda i, v: (6 + i * (w - 12) / (n - 1), 6 + (hi - v) * (h - 12) / span)  # noqa: E731
    def path(series):
        pts = [xy(i, v) for i, v in enumerate(series) if v is not None]
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    last = sp["closes"][-1]
    color = UP if last >= sp["closes"][0] else DOWN
    parts = [f'<polyline points="{path(ma)}" fill="none" stroke="#d4a843" stroke-width="1.6" stroke-dasharray="5 3"/>' if ma else "",
             f'<polyline points="{path(sp["closes"])}" fill="none" stroke="{color}" stroke-width="2.4"/>']
    if sp.get("low"):
        y = xy(0, sp["low"])[1]
        parts.append(f'<line x1="6" x2="{w - 6}" y1="{y:.1f}" y2="{y:.1f}" stroke="#94a3b8" stroke-width="1" stroke-dasharray="2 3"/>'
                     f'<text x="{w - 8}" y="{y - 4:.1f}" text-anchor="end" font-size="12" fill="#94a3b8">前低 {sp["low"]:,.0f}</text>')
    x, y = xy(n - 1, last)
    parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{color}"/>')
    legend = ('<div class="spleg"><span style="color:#d4a843">┄ 20 日線</span>'
              f'<span style="color:{color}">━ 近 20 日收盤</span></div>')
    return f'<svg class="spark" viewBox="0 0 {w} {h}" width="100%" height="{h}">{"".join(parts)}</svg>{legend}'


def rs_tag(s):
    """rs20：近 20 日報酬減大盤（上市比加權、上櫃比櫃買）的百分點"""
    if s.get("rs20") is None:
        return ""
    v = s["rs20"]
    return (f'<span class="rs" style="color:{sign_color(v)};border-color:{sign_color(v)}">'
            f'20 日{"強" if v > 0 else "弱"}於{esc(s.get("rs_base", "大盤"))} {abs(v):.1f} 個百分點</span>')


def weekly_table(w):
    """週報（週六版）：每檔的週漲跌、法人整週買賣超、這週價位守住幾次"""
    rows = "".join(
        f'<tr><td>{esc(r["name"])}</td><td class="r" style="color:{sign_color(r.get("chg"))}">{pct(r.get("chg"))}</td>'
        f'<td class="r" style="color:{sign_color(r.get("f"))}">{esc(r.get("f_text", ""))}</td>'
        f'<td class="r" style="color:{sign_color(r.get("s"))}">{esc(r.get("s_text", ""))}</td>'
        f'<td class="r">{esc(r.get("held", ""))}</td></tr>' for r in w.get("rows", []))
    return (f'<h2>本週回顧（{esc(w.get("range", ""))}）<span class="tagh">週報</span></h2>'
            '<table class="mini wk"><tr class="th"><td>股票</td><td class="r">週漲跌</td><td class="r">外資</td>'
            f'<td class="r">投信</td><td class="r">價位守住</td></tr>{rows}</table>'
            + (f'<div class="wknote">{esc(w["note"])}</div>' if w.get("note") else ""))


def stock_rows(stocks):
    out = []
    for s in stocks:
        c = sign_color(s.get("chg_pct"))
        ma = "".join(
            f'<span class="pill {"ok" if above else "bad"}">{label} {"上" if above else "下"}</span>'
            for label, above in s["ma_flags"])
        out.append(f'''
        <div class="stock">
          <div class="shead">
            <div><span class="sname">{esc(s["name"])}</span> <span class="sid">{esc(s["id"])}</span>
                 <span class="tag">{esc(s.get("tag", ""))}</span></div>
            <div class="sprice">{num(s["close"], 0)} <span style="color:{c}">{pct(s.get("chg_pct"))}</span></div>
          </div>
          {sparkline(s["spark"]) if s.get("spark") else ""}
          <div class="sline">{ma}<span class="muted">5 日 {pct(s.get("ret5"), 1)}｜20 日 {pct(s.get("ret20"), 1)}</span>{rs_tag(s)}</div>
          <table class="kv">
            <tr><td class="k">外資 5 日</td><td class="r" style="color:{sign_color(s.get("f5"))}">{esc(s["f5_text"])}</td>
                <td class="k">投信 5 日</td><td class="r" style="color:{sign_color(s.get("s5"))}">{esc(s["s5_text"])}</td></tr>
            <tr><td class="k">融資使用率</td><td class="r">{esc(s["margin_text"])}</td>
                <td class="k">大戶 400 張↑</td><td class="r">{esc(s["holder_text"])}</td></tr>
            <tr><td class="k">月營收年增</td><td class="r">{esc(s["rev_text"])}</td>
                <td class="k">選股評分</td><td class="r">{esc(s["score_text"])}</td></tr>
          </table>
          <div class="signal {s.get("signal_level", "neutral")}">{esc(s["signal"])}</div>
          {f'<div class="snews">📰 {esc(s["news"])}</div>' if s.get("news") else ""}
        </div>''')
    return "".join(out)


# 版型：dark＝深藍金色（預設）、paper＝白底報紙風、mobile＝手機直式大字單欄（搭配 "width": 720）
THEMES = {
    "dark": "",
    "paper": """
  body { background:#faf8f3; color:#1f2937; font-family:"Noto Serif CJK TC","WenQuanYi Zen Hei",serif; }
  .top { border-bottom:3px double #1f2937; } .brand { color:#8b1d1d; } h1 { color:#111827; }
  .dates { color:#4b5563; } .headline { background:#fff; color:#111827; border-left:6px solid #8b1d1d; box-shadow:0 1px 0 #e5e7eb; }
  h2 { color:#8b1d1d; border-bottom:1px solid #d1d5db; padding-bottom:4px; } h2 .tagh { background:#8b1d1d; color:#fff; }
  .q, .news, .stock, .review { background:#fff; border:1px solid #e5e7eb; box-shadow:none; }
  .qname, .nbody, .kv td, .rv, .snews, .wknote { color:#374151; } .qval, .ntitle, .sname, .sprice { color:#111827; }
  .qsub, .sid, .muted, .kv td.k, .spleg, .foot { color:#6b7280; } .mini td, .cal td { border-bottom:1px solid #e5e7eb; }
  .kv td { border-bottom:1px dashed #e5e7eb; } .tag { color:#8b1d1d; border-color:#8b1d1d; }
  .pill.ok { background:#fee2e2; color:#b91c1c; } .pill.bad { background:#dcfce7; color:#15803d; }
  .signal.bull { background:#fef2f2; color:#991b1b; } .signal.bear { background:#f0fdf4; color:#166534; } .signal.neutral { background:#f3f4f6; color:#1f2937; }
  .cal .time, .rvt { color:#8b1d1d; } .p2head { border-top:3px double #1f2937; color:#374151; } .foot { border-top:1px solid #d1d5db; }
  .rvmark.ok { color:#dc2626; } .rvmark.bad { color:#16a34a; }
""",
    "mobile": """
  body { background:#0f172a; color:#e2e8f0; }
  .page { padding:28px 22px 24px; }
  h1 { font-size:40px; } .top { flex-direction:column; align-items:flex-start; gap:6px; border-bottom:2px solid #38bdf8; }
  .brand { color:#38bdf8; } .dates { text-align:left; font-size:17px; }
  .headline { font-size:22px; background:#1e293b; border-left:6px solid #38bdf8; border-radius:10px; }
  h2 { color:#38bdf8; font-size:24px; } h2 .tagh { background:#38bdf8; color:#0f172a; }
  .two, .stocks { grid-template-columns:1fr !important; }
  .grid { grid-template-columns:repeat(2, 1fr) !important; }
  .q, .news, .stock, .review { background:#1e293b; border:1px solid #334155; border-radius:14px; }
  .qval { font-size:28px; } .qpct { font-size:22px; } .qname { font-size:17px; }
  .sname { font-size:26px; } .sprice { font-size:26px; } .kv td { font-size:16px; } .signal { font-size:17px; }
  .mini td, .cal td { font-size:18px; } ul.views { font-size:19px; } .nbody { font-size:17px; } .ntitle { font-size:20px; }
  .tag, .cal .time, .rvt { color:#38bdf8; border-color:#38bdf8; } .p2head { border-top:2px solid #38bdf8; flex-direction:column; gap:4px; }
""",
}


def build_html(d):
    news = "".join(f'<div class="news"><div class="ntitle">{esc(n["title"])}</div>'
                   f'<div class="nbody">{esc(n["body"])}</div></div>' for n in d["news"])
    tw = d["tw_market"]
    tw_rows = "".join(
        f'<tr><td>{esc(r["name"])}</td><td class="r">{esc(r["value"])}</td>'
        f'<td class="r" style="color:{sign_color(r.get("dir"))}">{esc(r.get("change", ""))}</td></tr>' for r in tw)
    cal = "".join(f'<tr><td class="time">{esc(c["time"])}</td><td>{esc(c["event"])}</td></tr>' for c in d["calendar"])
    week = "".join(f'<tr><td class="time">{esc(w["date"])}</td><td>{esc(w["event"])}</td>'
                   f'<td class="why">{esc(w.get("why", ""))}</td></tr>' for w in d.get("week_ahead", []))
    week_html = (f'<h2>未來一週注意 <span class="tagh">行事曆</span></h2><table class="cal week">{week}</table>'
                 if week else "")
    review = "".join(
        f'<div class="rv"><span class="rvmark {"ok" if r.get("ok") else "bad" if r.get("ok") is False else ""}">'
        f'{"✓" if r.get("ok") else "✗" if r.get("ok") is False else "・"}</span>{esc(r["item"])}：{esc(r["result"])}</div>'
        for r in d.get("review", []))
    review_html = (f'<div class="review"><div class="rvt">昨日看法回顧（{esc(d.get("review_date", ""))}）</div>{review}</div>'
                   if review else "")
    views = "".join(f'<li>{esc(v)}</li>' for v in d["views"])
    sources = "、".join(esc(s) for s in d["sources"])
    return f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:#0b1426; color:#e5e7eb; font-family:"WenQuanYi Zen Hei","Noto Sans CJK TC",sans-serif; }}
  .page {{ width:{d.get("width", 1080)}px; padding:36px 40px 28px; }}
  .top {{ display:flex; justify-content:space-between; align-items:flex-end; border-bottom:2px solid #d4a843; padding-bottom:14px; }}
  .brand {{ color:#d4a843; font-size:20px; letter-spacing:4px; }}
  h1 {{ margin:6px 0 0; font-size:46px; color:#fff; }}
  .dates {{ text-align:right; font-size:18px; color:#cbd5e1; line-height:1.6; }}
  .headline {{ margin:18px 0 6px; padding:16px 20px; background:#132241; border-left:6px solid #d4a843; font-size:23px; line-height:1.5; color:#fff; }}
  h2 {{ font-size:22px; color:#d4a843; margin:22px 0 10px; display:flex; gap:10px; align-items:center; }}
  h2 .tagh {{ background:#d4a843; color:#0b1426; font-size:15px; padding:2px 10px; border-radius:4px; }}
  .grid {{ display:grid; gap:10px; }}
  .q {{ background:#132241; border:1px solid #23365e; border-radius:10px; padding:10px 12px; }}
  .qname {{ font-size:16px; color:#cbd5e1; }}
  .qsub {{ font-size:12px; color:#7c8aa5; }}
  .qval {{ font-size:24px; font-weight:bold; color:#fff; margin-top:4px; }}
  .qpct {{ font-size:19px; font-weight:bold; }}
  .two {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
  table {{ width:100%; border-collapse:collapse; }}
  .mini td {{ padding:7px 6px; border-bottom:1px solid #23365e; font-size:17px; }}
  .r {{ text-align:right; }}
  .news {{ background:#132241; border-radius:10px; padding:12px 16px; margin-bottom:10px; }}
  .ntitle {{ font-size:19px; color:#fff; font-weight:bold; }}
  .nbody {{ font-size:16px; color:#cbd5e1; margin-top:4px; line-height:1.55; }}
  .stocks {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
  .stock {{ background:#132241; border:1px solid #23365e; border-radius:12px; padding:12px 14px; }}
  .shead {{ display:flex; justify-content:space-between; align-items:baseline; }}
  .sname {{ font-size:22px; color:#fff; font-weight:bold; }}
  .sid {{ color:#94a3b8; font-size:16px; }}
  .tag {{ font-size:12px; color:#d4a843; border:1px solid #d4a843; border-radius:4px; padding:0 6px; margin-left:4px; }}
  .sprice {{ font-size:22px; font-weight:bold; color:#fff; }}
  .sline {{ margin:8px 0; display:flex; flex-wrap:wrap; gap:6px; align-items:center; font-size:14px; }}
  .pill {{ border-radius:999px; padding:1px 9px; font-size:13px; }}
  .pill.ok {{ background:rgba(220,38,38,.18); color:#fca5a5; }}
  .pill.bad {{ background:rgba(22,163,74,.18); color:#86efac; }}
  .muted {{ color:#94a3b8; margin-left:4px; }}
  .kv td {{ font-size:14px; padding:4px 3px; color:#cbd5e1; border-bottom:1px dashed #23365e; line-height:1.35; }}
  .kv td.k {{ white-space:nowrap; color:#94a3b8; width:1%; }}
  .signal {{ margin-top:8px; font-size:15px; padding:8px 10px; border-radius:8px; line-height:1.5; }}
  .signal.bull {{ background:rgba(220,38,38,.14); color:#fecaca; }}
  .signal.bear {{ background:rgba(22,163,74,.14); color:#bbf7d0; }}
  .signal.neutral {{ background:#1c2d52; color:#e2e8f0; }}
  .cal td {{ padding:7px 6px; border-bottom:1px solid #23365e; font-size:17px; }}
  .week .why {{ color:#94a3b8; font-size:15px; width:34%; }}
  .cal .time {{ color:#d4a843; width:110px; white-space:nowrap; }}
  ul.views {{ margin:0; padding-left:22px; font-size:17.5px; line-height:1.65; }}
  .review {{ margin:10px 0 4px; padding:10px 16px; background:#101c36; border:1px solid #23365e; border-radius:10px; }}
  .rvt {{ color:#d4a843; font-size:16px; margin-bottom:4px; }}
  .rv {{ font-size:16px; line-height:1.6; color:#cbd5e1; }}
  .rvmark {{ display:inline-block; width:22px; font-weight:bold; color:#94a3b8; }}
  .rvmark.ok {{ color:#fca5a5; }} .rvmark.bad {{ color:#86efac; }}
  .p2head {{ margin-top:30px; padding:14px 0 8px; border-top:2px solid #d4a843; display:flex; justify-content:space-between; font-size:18px; color:#cbd5e1; }}
  .p2head .brand {{ font-size:16px; }}
  .spark {{ display:block; margin:6px 0 0; }}
  .spleg {{ display:flex; gap:14px; font-size:12px; margin:-2px 0 2px; }}
  .rs {{ border:1px solid; border-radius:999px; padding:0 8px; font-size:12.5px; margin-left:4px; }}
  .snews {{ margin-top:6px; font-size:14px; color:#cbd5e1; line-height:1.5; }}
  .wk .th td {{ color:#94a3b8; font-size:14px; }}
  .wknote {{ font-size:15px; color:#cbd5e1; margin-top:6px; }}
  .foot {{ margin-top:22px; font-size:12.5px; color:#7c8aa5; line-height:1.6; border-top:1px solid #23365e; padding-top:10px; }}
{THEMES.get(d.get("theme", "dark"), "")}
</style></head><body><div class="page">
  <div class="top">
    <div><div class="brand">RAYMOND 台股研究｜每日早報</div><h1>{esc(d["title"])}</h1></div>
    <div class="dates">台股 {esc(d["date_tw"])}<br>美股 {esc(d["us_date"])} 收盤</div>
  </div>
  <div class="headline">{esc(d["headline"])}</div>
  {review_html}

  <h2>美股四大指數 <span class="tagh">戰況</span></h2>
  {quote_cards(d["us_index"], 5)}

  <div class="two">
    <div><h2>美債・原油・匯率</h2>{rates_table(d["rates"])}</div>
    <div><h2>台灣 ADR</h2>{quote_cards(d["adr"], 2)}</div>
  </div>

  <h2>科技七巨頭</h2>
  {quote_cards(d["mag7"], 7)}
  <h2>AI・半導體焦點</h2>
  {quote_cards(d["semi"], 4)}

  <h2>昨夜到今晨重點 <span class="tagh">新聞</span></h2>
  {news}

  <div id="part2" class="p2head"><span class="brand">RAYMOND 台股研究｜每日早報</span><span>{esc(d["title"])}・台股與關注股</span></div>
  {weekly_table(d["weekly"]) if d.get("weekly") else ""}
  <h2>台股前一交易日（{esc(d["tw_prev_date"])}）<span class="tagh">資料庫</span></h2>
  <table class="mini">{tw_rows}</table>

  <h2>我的關注股 <span class="tagh">籌碼＋技術</span></h2>
  <div class="stocks">{stock_rows(d["stocks"])}</div>

  <div class="two">
    <div><h2>今日行事曆</h2><table class="cal">{cal}</table></div>
    <div><h2>今天怎麼看（推論）</h2><ul class="views">{views}</ul></div>
  </div>

  {week_html}

  <div class="foot">資料來源：{sources}<br>{esc(d["data_note"])}<br>
  以上為資料整理與個人研究，不構成投資建議；標示「推論」的內容是判斷，不是事實。</div>
</div></body></html>'''


def render(html_path, png_path, pdf_path, width=1080):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 1200}, device_scale_factor=2)
        page.goto(html_path.resolve().as_uri())
        page.wait_for_timeout(300)
        page.screenshot(path=str(png_path), full_page=True)
        height = page.evaluate("document.documentElement.scrollHeight")
        split = page.evaluate("(() => { const e = document.getElementById('part2'); return e ? e.getBoundingClientRect().top + window.scrollY : 0 })()")
        if split:  # 手機好讀：另存美股、台股兩張
            stem = png_path.with_suffix("")
            for suffix, (top, bottom) in (("-1美股", (0, split)), ("-2台股", (split, height))):
                page.screenshot(path=f"{stem}{suffix}.png", full_page=True,
                                clip={"x": 0, "y": top, "width": width, "height": bottom - top})
        page.pdf(path=str(pdf_path), width=f"{width}px", height=f"{height + 2}px", print_background=True,
                 margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
        browser.close()


def main():
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    base = out / f"台股早報-{data['date_tw']}"
    html_path = base.with_suffix(".html")
    layout = data.get("layout", "listcard")
    if layout in ("list", "listcard"):  # 預設 listcard：H 深色章節＋卡片；list：H 深色表格（full_layouts.py）
        import full_layouts
        html = (full_layouts.listing_cards if layout == "listcard" else full_layouts.listing)(data)
    else:  # layout: "classic" 用原本的卡片版
        html = build_html(data)
    html_path.write_text(html, encoding="utf-8")
    render(html_path, base.with_suffix(".png"), base.with_suffix(".pdf"), data.get("width", 1080))
    for suffix in ("-1美股", "-2台股"):
        print(f"{base}{suffix}.png")
    print(base.with_suffix(".pdf"))


if __name__ == "__main__":
    main()
