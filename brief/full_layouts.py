"""完整內容版型：每一版都放進早報的全部資料，只換排版。
用法：python3 full_layouts.py <data.json> <outdir> <split|list|wide>
"""
import json
import sys
from pathlib import Path

from build_brief import esc, num, pct, sign_color, sparkline

FONT = "font-family:'WenQuanYi Zen Hei','Noto Sans CJK TC',sans-serif;"


# ───── 共用區塊（每個版型都用同一份內容）─────
def q_table(items, title):
    rows = "".join(f'<tr><td>{esc(q["name"])}</td><td class="r">{num(q["close"])}</td>'
                   f'<td class="r b" style="color:{sign_color(q["chg_pct"])}">{pct(q["chg_pct"])}</td></tr>'
                   + (f'<tr class="subr"><td colspan="3">{esc(q["sub"])}</td></tr>' if q.get("sub") else "") for q in items)
    return f'<h2>{title}</h2><table class="t">{rows}</table>'


def kv_table(rows, title):
    body = "".join(f'<tr><td>{esc(r["name"])}</td><td class="r b" style="color:{sign_color(r.get("dir"))}">{esc(r["value"])}</td>'
                   f'<td class="r m">{esc(r.get("change", ""))}</td></tr>' for r in rows)
    return f'<h2>{title}</h2><table class="t">{body}</table>'


def review(d):
    mark = {True: ("✓", "#dc2626"), False: ("✗", "#16a34a"), None: ("–", "#64748b")}
    rows = "".join(f'<tr><td class="ok" style="color:{mark[r["ok"]][1]}">{mark[r["ok"]][0]}</td><td class="b">{esc(r["item"])}</td>'
                   f'<td class="m">{esc(r["result"])}</td></tr>' for r in d.get("review", []))
    return f'<h2>昨日點名回顧（{esc(d.get("review_date", ""))}）</h2><table class="t">{rows}</table>' if rows else ""


def news(d):
    return "<h2>昨夜到今晨重點</h2>" + "".join(
        f'<div class="nw"><div class="b">{esc(n["title"])}</div><div class="m">{esc(n["body"])}</div></div>' for n in d["news"])


def calendar(d):
    rows = "".join(f'<tr><td class="b nw2">{esc(c["time"])}</td><td>{esc(c["event"])}</td></tr>' for c in d["calendar"])
    return f'<h2>今日行事曆</h2><table class="t">{rows}</table>'


def week(d):
    rows = "".join(f'<tr><td class="b nw2">{esc(w["date"])}</td><td>{esc(w["event"])}<div class="m">{esc(w.get("why", ""))}</div></td></tr>'
                   for w in d.get("week_ahead", []))
    return f'<h2>未來一週注意</h2><table class="t">{rows}</table>' if rows else ""


def views(d):
    return "<h2>今天怎麼看（推論）</h2><ol class='vw'>" + "".join(f"<li>{esc(v)}</li>" for v in d["views"]) + "</ol>"


def weekly(d):
    w = d.get("weekly")
    if not w:
        return ""
    rows = "".join(f'<tr><td class="b">{esc(r["name"])}</td><td class="r" style="color:{sign_color(r["chg"])}">{pct(r["chg"])}</td>'
                   f'<td class="r">{esc(r.get("f_text", ""))}</td><td class="r">{esc(r.get("s_text", ""))}</td><td>{esc(r.get("held", ""))}</td></tr>'
                   for r in w["rows"])
    head = '<tr class="m"><td>股票</td><td class="r">週漲跌</td><td class="r">外資整週</td><td class="r">投信整週</td><td>價位守住</td></tr>'
    return f'<h2>本週回顧（{esc(w.get("range", ""))}）</h2><table class="t">{head}{rows}</table><div class="m">{esc(w.get("note", ""))}</div>'


def ma_text(s):
    return "　".join(f'{esc(lab)}{"上" if up else "下"}' for lab, up in s["ma_flags"])


def rs_text(s):
    return f'{"+" if s["rs20"] > 0 else ""}{s["rs20"]:.1f}（對{esc(s["rs_base"])}）' if s.get("rs20") is not None else "–"


STOCK_ROWS = [("5 日／20 日", lambda s: f'<span style="color:{sign_color(s["ret5"])}">{pct(s["ret5"])}</span>／<span style="color:{sign_color(s["ret20"])}">{pct(s["ret20"])}</span>'),
              ("20 日相對強弱", lambda s: f'<span style="color:{sign_color(s.get("rs20"))}">{rs_text(s)}</span>'),
              ("均線", ma_text),
              ("外資 5 日", lambda s: f'<span style="color:{sign_color(s["f5"])}">{esc(s["f5_text"])}</span>'),
              ("投信 5 日", lambda s: f'<span style="color:{sign_color(s["s5"])}">{esc(s["s5_text"])}</span>'),
              ("融資使用率", lambda s: esc(s["margin_text"])),
              ("大戶持股", lambda s: esc(s["holder_text"])),
              ("月營收年增", lambda s: esc(s["rev_text"])),
              ("系統評分", lambda s: esc(s["score_text"]))]


def stock_card(s, spark_w):
    kv = "".join(f'<tr><td class="k">{lab}</td><td>{f(s)}</td></tr>' for lab, f in STOCK_ROWS)
    nw = f'<div class="m">📰 {esc(s["news"])}</div>' if s.get("news") else ""
    return (f'<div class="sc"><div class="sh"><span class="sn">{esc(s["name"])}</span> <span class="m">{esc(s["id"])}・{esc(s["tag"])}</span>'
            f'<span class="sp">{num(s["close"], 0)} <b style="color:{sign_color(s["chg_pct"])}">{pct(s["chg_pct"])}</b></span></div>'
            f'{sparkline(s["spark"], w=spark_w, h=70) if s.get("spark") else ""}<table class="t kv">{kv}</table>'
            f'<div class="sig {esc(s.get("signal_level", ""))}">{esc(s["signal"])}</div>{nw}</div>')


def stock_matrix(d, spark_w=200):
    ss = d["stocks"]
    head = "".join(f'<th><div class="sn">{esc(s["name"])}</div><div class="m">{esc(s["id"])}・{esc(s["tag"])}</div>'
                   f'<div class="sp">{num(s["close"], 0)} <b style="color:{sign_color(s["chg_pct"])}">{pct(s["chg_pct"])}</b></div></th>' for s in ss)
    spk = "".join(f'<td>{sparkline(s["spark"], w=spark_w, h=56) if s.get("spark") else ""}</td>' for s in ss)
    body = "".join(f'<tr><td class="k">{lab}</td>' + "".join(f"<td>{f(s)}</td>" for s in ss) + "</tr>" for lab, f in STOCK_ROWS)
    sig = "".join(f'<td class="sig {esc(s.get("signal_level", ""))}">{esc(s["signal"])}</td>' for s in ss)
    nws = "".join(f'<td class="m">{esc(s.get("news") or "")}</td>' for s in ss) if any(s.get("news") for s in ss) else ""
    return (f'<h2>關注股對照表</h2><table class="t mx"><tr><th></th>{head}</tr><tr><td class="k">近 60 日</td>{spk}</tr>{body}'
            f'<tr><td class="k">訊號</td>{sig}</tr>' + (f'<tr><td class="k">個股新聞</td>{nws}</tr>' if nws else "") + "</table>")


def foot(d):
    return f'<div class="ft">資料來源：{"、".join(esc(x) for x in d["sources"])}<br>{esc(d["data_note"])}</div>'


BASE = f"""* {{ box-sizing:border-box; }} body {{ margin:0; {FONT} }}
h2 {{ font-size:21px; margin:22px 0 8px; }} .t {{ width:100%; border-collapse:collapse; font-size:16px; }}
.t td, .t th {{ padding:6px 8px; vertical-align:top; line-height:1.5; }} .r {{ text-align:right; white-space:nowrap; }}
.b {{ font-weight:bold; }} .m {{ font-size:14.5px; opacity:.75; line-height:1.55; }} .nw2 {{ white-space:nowrap; }}
.subr td {{ font-size:14px; opacity:.7; padding-top:0; }} .ok {{ font-size:20px; font-weight:bold; width:28px; }}
.nw {{ margin-bottom:12px; font-size:16px; }} .vw {{ font-size:16px; line-height:1.65; padding-left:22px; margin:0; }} .vw li {{ margin-bottom:6px; }}
.kv td.k, .mx td.k {{ white-space:nowrap; opacity:.7; font-size:14.5px; }} .sn {{ font-size:22px; font-weight:bold; }}
.sp {{ font-size:20px; font-weight:bold; }} .sig {{ font-size:15px; line-height:1.55; border-radius:8px; padding:8px 10px; }}
.sig.bear {{ background:rgba(22,163,74,.10); }} .sig.bull {{ background:rgba(220,38,38,.10); }} .sig.neutral {{ background:rgba(148,163,184,.15); }}
.ft {{ font-size:12px; opacity:.6; margin-top:24px; line-height:1.6; }} svg {{ display:block; }}
"""


# ───── G. 左右對照：左美股、右台股 ─────
def split(d):
    css = BASE + """body { background:#f8fafc; color:#0f172a; } .page { width:1600px; padding:36px 40px; }
.top { background:#0f172a; color:#fff; border-radius:16px; padding:22px 28px; } .top h1 { margin:0; font-size:36px; } .top .hl { font-size:19px; margin-top:8px; line-height:1.6; color:#e2e8f0; }
.cols { display:grid; grid-template-columns:1fr 1fr; gap:28px; margin-top:20px; }
.col { background:#fff; border-radius:16px; padding:8px 24px 20px; border-top:8px solid; }
.col.us { border-color:#2563eb; } .col.tw { border-color:#dc2626; } .cap { font-size:26px; font-weight:bold; margin:16px 0 0; }
.t tr { border-bottom:1px solid #f1f5f9; } .sc { border:1px solid #e2e8f0; border-radius:12px; padding:12px 14px; margin-bottom:12px; }
.sh { display:flex; align-items:baseline; gap:8px; } .sh .sp { margin-left:auto; } .g2 { display:grid; grid-template-columns:1fr 1fr; gap:18px; }
.bot { display:grid; grid-template-columns:1fr 1fr 1fr; gap:28px; background:#fff; border-radius:16px; padding:8px 24px 20px; margin-top:24px; }
"""
    return f'''<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body><div class="page">
<div class="top"><h1>{esc(d["title"])}</h1><div class="hl">{esc(d["headline"])}</div></div>
<div class="cols"><div class="col us"><div class="cap">🇺🇸 美股（{esc(d["us_date"])} 收盤）</div>
{q_table(d["us_index"], "四大指數")}<div class="g2"><div>{kv_table(d["rates"], "美債・原油・匯率")}</div><div>{q_table(d["adr"], "台灣 ADR")}</div></div>
<div class="g2"><div>{q_table(d["mag7"], "科技七巨頭")}</div><div>{q_table(d["semi"], "AI・半導體")}</div></div>{news(d)}</div>
<div class="col tw"><div class="cap">🇹🇼 台股（{esc(d["tw_prev_date"])}）</div>{review(d)}{kv_table(d["tw_market"], "大盤與法人")}
<h2>我的關注股</h2>{"".join(stock_card(s, 660) for s in d["stocks"])}</div></div>
<div class="bot"><div>{calendar(d)}</div><div>{week(d)}</div><div>{views(d)}</div></div>{weekly(d)}{foot(d)}</div></body></html>'''


# ───── H. 清單表格式：單欄、編號章節、關注股橫向對照 ─────
def listing(d):
    secs = [("今天一句話", f'<div class="hl">{esc(d["headline"])}</div>'), ("昨日點名回顧", review(d)),
            ("美股收盤", q_table(d["us_index"], "四大指數") + kv_table(d["rates"], "美債・原油・匯率")),
            ("台灣 ADR 與科技股", q_table(d["adr"], "台灣 ADR") + q_table(d["mag7"], "科技七巨頭") + q_table(d["semi"], "AI・半導體")),
            ("新聞", news(d)), ("台股大盤與法人", kv_table(d["tw_market"], f'前一交易日（{esc(d["tw_prev_date"])}）')),
            ("關注股", stock_matrix(d)), ("今天怎麼看", views(d)), ("行事曆", calendar(d) + week(d))]
    if d.get("weekly"):
        secs.append(("本週回顧", weekly(d)))
    body = "".join(f'<section{" id='part2'" if t.startswith("台股") else ""}><div class="no">{i:02d}</div><div class="sb"><div class="st">{t}</div>{h.replace("<h2>", "<h3>").replace("</h2>", "</h3>")}</div></section>'
                   for i, (t, h) in enumerate(secs, 1))
    css = BASE + """body { background:#0b1220; color:#e5e7eb; } .page { width:1080px; padding:44px 48px; }
h1 { font-size:40px; margin:0 0 6px; color:#fff; } .dt { color:#94a3b8; font-size:16px; }
section { display:grid; grid-template-columns:70px 1fr; border-top:2px solid #475569; padding:16px 0 10px; margin-top:14px; }
.no { font-size:34px; font-weight:bold; color:#38bdf8; opacity:.55; } .st { font-size:24px; font-weight:bold; margin-bottom:4px; color:#fff; }
h3 { font-size:17px; color:#93c5fd; margin:12px 0 4px; } .hl { font-size:20px; line-height:1.65; color:#fde68a; }
.t tr { border-bottom:1px solid #1e293b; } .mx th { text-align:left; padding:6px 8px; border-bottom:2px solid #334155; } .mx td { font-size:14.5px; }
.sig.bear { background:rgba(22,163,74,.20); } .sig.bull { background:rgba(220,38,38,.20); } .sig.neutral { background:rgba(148,163,184,.18); }
.mx .sn { font-size:19px; } .mx .sp { font-size:16px; } .mx .sig { font-size:13.5px; }
"""
    return f'''<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body><div class="page">
<h1>{esc(d["title"])}</h1><div class="dt">美股 {esc(d["us_date"])} 收盤・台股 {esc(d["tw_prev_date"])}</div>{body}{foot(d)}</div></body></html>'''


# ───── I. 橫式全景：三欄＋底部四檔 ─────
def wide(d):
    css = BASE + """body { background:#0b1220; color:#e2e8f0; } .page { width:1920px; padding:30px 36px; }
.hd { display:flex; align-items:baseline; gap:26px; border-bottom:2px solid #334155; padding-bottom:14px; }
.hd h1 { margin:0; font-size:38px; white-space:nowrap; } .hd .hl { font-size:19px; color:#cbd5e1; line-height:1.5; }
.g3 { display:grid; grid-template-columns:1fr 1.1fr 1fr; gap:22px; margin-top:8px; }
.pn { background:#111a2e; border:1px solid #1e293b; border-radius:14px; padding:4px 18px 16px; } .pn h2 { color:#93c5fd; }
.t tr { border-bottom:1px solid #1e293b; } .g2 { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
.g4 { display:grid; grid-template-columns:repeat(4,1fr); gap:18px; margin-top:22px; }
.sc { background:#111a2e; border:1px solid #1e293b; border-radius:14px; padding:12px 14px; }
.sh { display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; } .sh .sp { margin-left:auto; }
.sig.bear { background:rgba(22,163,74,.18); } .sig.bull { background:rgba(220,38,38,.18); }
"""
    return f'''<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body><div class="page">
<div class="hd"><h1>{esc(d["title"])}</h1><div class="hl">{esc(d["headline"])}</div></div>
<div class="g3"><div class="pn">{q_table(d["us_index"], f'美股（{esc(d["us_date"])}）')}{kv_table(d["rates"], "美債・原油・匯率")}
<div class="g2"><div>{q_table(d["mag7"], "科技七巨頭")}</div><div>{q_table(d["semi"], "AI・半導體")}</div></div></div>
<div class="pn">{q_table(d["adr"], "台灣 ADR")}{news(d)}</div>
<div class="pn">{review(d)}{kv_table(d["tw_market"], f'台股（{esc(d["tw_prev_date"])}）')}{views(d)}</div></div>
<div class="g4">{"".join(stock_card(s, 420) for s in d["stocks"])}</div>
<div class="g2" style="margin-top:22px"><div class="pn">{calendar(d)}</div><div class="pn">{week(d)}</div></div>{weekly(d)}{foot(d)}</div></body></html>'''


LAYOUTS = {"split": (split, 1600), "list": (listing, 1080), "wide": (wide, 1920)}


def main():
    d = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    from layouts import shoot
    fn, width = LAYOUTS[sys.argv[3]]
    path = out / f"台股早報-{d['date_tw']}-{sys.argv[3]}.png"
    shoot(fn(d), path, width)
    print(path)


if __name__ == "__main__":
    main()


# ───── H＋卡片：H 深色章節，內容改用卡片 ─────
def q_cards(items, title, cols=4):
    def card(q):
        c, a = sign_color(q["chg_pct"]), min(abs(q["chg_pct"]) / 5, 1) * .28 + .06
        bg = f"rgba(220,38,38,{a:.2f})" if q["chg_pct"] > 0 else f"rgba(22,163,74,{a:.2f})" if q["chg_pct"] < 0 else "#162036"
        sub = f'<div class="qs">{esc(q["sub"])}</div>' if q.get("sub") else ""
        return (f'<div class="qc" style="background:{bg};border-color:{c}"><div class="qn">{esc(q["name"])}</div>'
                f'<div class="qv">{num(q["close"])}</div><div class="qp" style="color:{c}">{pct(q["chg_pct"])}</div>{sub}</div>')
    return f'<h3>{title}</h3><div class="qg" style="grid-template-columns:repeat({cols},1fr)">{"".join(card(q) for q in items)}</div>'


def r_cards(rows, title, cols=4):
    cards = "".join(f'<div class="qc rc"><div class="qn">{esc(r["name"])}</div><div class="qv" style="color:{sign_color(r.get("dir"))}">{esc(r["value"])}</div>'
                    f'<div class="qs">{esc(r.get("change", ""))}</div></div>' for r in rows)
    return f'<h3>{title}</h3><div class="qg" style="grid-template-columns:repeat({cols},1fr)">{cards}</div>'


def listing_cards(d):
    secs = [("今天一句話", f'<div class="hl">{esc(d["headline"])}</div>'), ("昨日點名回顧", review(d)),
            ("美股收盤", q_cards(d["us_index"], "四大指數", 5) + r_cards(d["rates"], "美債・原油・匯率")),
            ("台灣 ADR 與科技股", q_cards(d["adr"], "台灣 ADR") + q_cards(d["mag7"], "科技七巨頭") + q_cards(d["semi"], "AI・半導體")),
            ("新聞", "".join(f'<div class="nc"><div class="b">{esc(n["title"])}</div><div class="m">{esc(n["body"])}</div></div>' for n in d["news"])),
            ("台股大盤與法人", r_cards(d["tw_market"], f'前一交易日（{esc(d["tw_prev_date"])}）', 3)),
            ("關注股", '<div class="sg2">' + "".join(stock_card(s, 440) for s in d["stocks"]) + "</div>"),
            ("今天怎麼看", views(d)), ("行事曆", f'<div class="cg"><div class="mc">{calendar(d)}</div><div class="mc">{week(d)}</div></div>')]
    if d.get("weekly"):
        secs.append(("本週回顧", weekly(d)))
    body = "".join(f'<section{" id='part2'" if t.startswith("台股") else ""}><div class="sb"><span class="no">{i:02d}</span><div class="st">{t}</div>{h.replace("<h2>", "<h3>").replace("</h2>", "</h3>")}</div></section>'
                   for i, (t, h) in enumerate(secs, 1))
    css = BASE + """body { background:#070c17; color:#e5e7eb; } .page { width:1080px; padding:40px 40px; }
h1 { font-size:40px; margin:0 0 6px; color:#fff; } .dt { color:#94a3b8; font-size:16px; }
section { display:block; background:#111a2e; border:1px solid #22304a; border-radius:18px; padding:18px 22px 18px; margin-top:18px; box-shadow:0 6px 18px rgba(0,0,0,.35); }
.no { display:inline-block; font-size:18px; font-weight:bold; color:#0b1220; background:#38bdf8; border-radius:8px; padding:2px 10px; margin-right:10px; vertical-align:4px; }
.st { display:inline; font-size:24px; font-weight:bold; color:#fff; } .sb > h3:first-of-type { margin-top:12px; }
.t tr { border-bottom:1px solid #22304a; }
h3 { font-size:17px; color:#93c5fd; margin:14px 0 8px; } .hl { font-size:20px; line-height:1.65; color:#fde68a; }
.t tr { border-bottom:1px solid #1e293b; }
.qg { display:grid; gap:10px; } .qc { border:1px solid; border-left-width:5px; border-radius:10px; padding:10px 12px; }
.qc.rc { background:#0b1220; border-color:#22304a; border-left-color:#475569; }
.qn { font-size:14.5px; color:#cbd5e1; } .qv { font-size:22px; font-weight:bold; color:#fff; margin-top:2px; } .rc .qv { font-size:19px; }
.qp { font-size:18px; font-weight:bold; } .qs { font-size:13px; color:#94a3b8; margin-top:3px; line-height:1.4; }
.nc { background:#0b1220; border:1px solid #22304a; border-left:5px solid #38bdf8; border-radius:10px; padding:12px 14px; margin-bottom:10px; font-size:16.5px; }
.cg { display:grid; grid-template-columns:1fr 1.3fr; gap:14px; margin-top:12px; } .mc { background:#0b1220; border:1px solid #22304a; border-radius:12px; padding:2px 12px 8px; }
.mc h3 { margin-top:10px; font-size:18px; } .mc .t td { font-size:15px; }
.sg2 { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
.sc { background:#0b1220; border:1px solid #22304a; border-radius:12px; padding:12px 14px; } .sh { display:flex; align-items:baseline; gap:6px; flex-wrap:wrap; } .sh .sp { margin-left:auto; }
.kv td { padding:4px 6px; font-size:14.5px; } .sig.bear { background:rgba(22,163,74,.20); } .sig.bull { background:rgba(220,38,38,.20); } .sig.neutral { background:rgba(148,163,184,.18); }
"""
    return f'''<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body><div class="page">
<h1>{esc(d["title"])}</h1><div class="dt">美股 {esc(d["us_date"])} 收盤・台股 {esc(d["tw_prev_date"])}</div>{body}{foot(d)}</div></body></html>'''


LAYOUTS["listcard"] = (listing_cards, 1080)
