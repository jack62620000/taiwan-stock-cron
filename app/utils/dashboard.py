"""個股分析卡的儀表板元件：面板樣式、儀表、雷達圖、紅綠燈、多空能量條（都輸出 HTML／SVG 字串）"""
import math

from utils.ui import UP_COLOR, DOWN_COLOR, WARN_COLOR, ACCENT, GLOW, MUTED

DIM = "#1e3a6e"

# 面板外框：深藍底、青色發光邊框、左上角編號
DASH_CSS = f"""
.dash{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;}}
.span2{{grid-column:span 2;}} .span3{{grid-column:span 3;}}
@media(max-width:900px){{.dash{{grid-template-columns:1fr;}}.span2,.span3{{grid-column:span 1;}}}}
.pnl{{background:linear-gradient(180deg,#0d1b3a,#0a1530);border:1px solid {DIM};border-radius:8px;padding:12px 14px;
      box-shadow:0 0 0 1px rgba(56,189,248,.08),0 0 14px rgba(56,189,248,.18) inset;position:relative;}}
.pt2{{display:flex;align-items:center;gap:8px;font-size:15px;font-weight:700;color:#e2e8f0;margin-bottom:10px;}}
.pno{{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border:1px solid {GLOW};
      border-radius:4px;color:{GLOW};font-family:"IBM Plex Mono",monospace;font-size:13px;box-shadow:0 0 6px rgba(56,189,248,.5);}}
.psub{{font-size:11px;color:{MUTED};font-weight:400;margin-left:auto;}}
.verdict{{margin-top:10px;padding:7px 10px;border:1px solid {DIM};border-radius:6px;font-size:12.5px;text-align:center;}}
.gauges{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:4px;text-align:center;}}
@media(max-width:700px){{.gauges{{grid-template-columns:repeat(3,minmax(0,1fr));}}.psub{{display:none;}}}}
.gl{{font-size:11.5px;color:{MUTED};}} .gs{{font-size:12px;font-weight:700;margin-top:-2px;}}
.grade{{display:flex;flex-direction:column;gap:8px;}}
.gbox{{border:1px solid {DIM};border-radius:6px;padding:8px;text-align:center;background:rgba(5,11,26,.5);}}
.gbig{{font-family:"IBM Plex Mono",monospace;font-size:44px;font-weight:700;line-height:1;color:{ACCENT};
       text-shadow:0 0 12px rgba(251,191,36,.5);}}
.lights{{display:flex;gap:14px;align-items:center;}}
.lrow{{display:flex;flex-direction:column;gap:8px;flex:1;}}
.lbl{{border:1px solid {DIM};border-radius:6px;padding:6px 10px;font-size:12.5px;color:{MUTED};}}
.lbl.on{{color:#e2e8f0;font-weight:700;box-shadow:0 0 10px currentColor inset;}}
.ebar{{display:flex;align-items:center;gap:10px;margin-bottom:10px;}}
.epct{{font-family:"IBM Plex Mono",monospace;font-size:30px;font-weight:700;line-height:1;}}
.eblk{{display:flex;gap:2px;flex:1;}} .eblk span{{flex:1;height:18px;border-radius:2px;}}
.etab{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px 16px;font-size:12.5px;}}
.etab div{{display:flex;justify-content:space-between;border-bottom:1px dashed {DIM};padding:3px 0;}}
"""


def level_color(v):
    """0～100：70 以上紅（強、熱）、40～70 金、40 以下綠（弱、冷）"""
    if v is None:
        return MUTED
    return UP_COLOR if v >= 70 else ACCENT if v >= 40 else DOWN_COLOR


def gauge_svg(value, color=None):
    """半圓儀表，value 0～100（None 顯示灰色）"""
    cx, cy, r = 60, 58, 44
    color = color or level_color(value)

    def pt(f):
        a = math.pi * (1 - f)
        return cx + r * math.cos(a), cy - r * math.sin(a)

    x0, y0 = pt(0)
    x1, y1 = pt(1)
    arcs = f'<path d="M{x0:.1f},{y0:.1f} A{r},{r} 0 0 1 {x1:.1f},{y1:.1f}" stroke="{DIM}" stroke-width="9" fill="none"/>'
    if value is None:
        text = "—"
    else:
        f = min(max(value, 0), 100) / 100
        xv, yv = pt(f)
        if f > 0:
            arcs += (f'<path d="M{x0:.1f},{y0:.1f} A{r},{r} 0 0 1 {xv:.1f},{yv:.1f}" stroke="{color}" stroke-width="9" '
                     f'fill="none" style="filter:drop-shadow(0 0 3px {color})"/>')
        nx, ny = cx + (r - 14) * math.cos(math.pi * (1 - f)), cy - (r - 14) * math.sin(math.pi * (1 - f))
        arcs += (f'<line x1="{cx}" y1="{cy}" x2="{nx:.1f}" y2="{ny:.1f}" stroke="#e2e8f0" stroke-width="2.5" '
                 f'stroke-linecap="round"/><circle cx="{cx}" cy="{cy}" r="4" fill="#e2e8f0"/>')
        text = f"{value:.0f}"
    return (f'<svg viewBox="0 0 120 84" width="100%" style="max-width:130px">{arcs}'
            f'<text x="{cx}" y="80" text-anchor="middle" font-size="15" font-weight="700" fill="#e2e8f0" '
            f'font-family="IBM Plex Mono, monospace">{text}</text></svg>')


def radar_svg(axes):
    """雷達圖：axes 是 [(名稱, 0～10 分或 None), ...]，None 畫在 0 並顯示 —"""
    cx, cy, R = 150, 128, 84
    n = len(axes)

    def pt(i, v):
        a = -math.pi / 2 + 2 * math.pi * i / n
        return cx + R * v / 10 * math.cos(a), cy + R * v / 10 * math.sin(a)

    parts = []
    for lv in (2.5, 5, 7.5, 10):
        ring = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, lv) for i in range(n)))
        parts.append(f'<polygon points="{ring}" fill="none" stroke="{DIM}" stroke-width="1"/>')
    for i in range(n):
        x, y = pt(i, 10)
        parts.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="{DIM}" stroke-width="1"/>')
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, v or 0) for i, (_, v) in enumerate(axes)))
    parts.append(f'<polygon points="{poly}" fill="rgba(251,191,36,.28)" stroke="{ACCENT}" stroke-width="2" '
                 f'style="filter:drop-shadow(0 0 4px rgba(251,191,36,.6))"/>')
    for i, (name, v) in enumerate(axes):
        x, y = pt(i, 12.4)
        anchor = "middle" if abs(x - cx) < 10 else "start" if x > cx else "end"
        parts.append(f'<text x="{x:.1f}" y="{y - 2:.1f}" text-anchor="{anchor}" font-size="12" fill="#cbd5e1">{name}</text>'
                     f'<text x="{x:.1f}" y="{y + 13:.1f}" text-anchor="{anchor}" font-size="13" font-weight="700" '
                     f'fill="{ACCENT}" font-family="IBM Plex Mono, monospace">{"—" if v is None else f"{v:.1f}"}</text>')
    return f'<svg viewBox="0 0 300 262" width="100%" style="max-width:340px">{"".join(parts)}</svg>'


LIGHTS = [("red", UP_COLOR), ("yellow", "#facc15"), ("green", DOWN_COLOR), ("gray", "#64748b")]


def traffic_light_svg(active):
    """直立紅綠燈：active 是 red／yellow／green／gray"""
    parts = ['<rect x="8" y="4" width="64" height="232" rx="16" fill="#060d1f" stroke="#334155" stroke-width="2"/>']
    for k, (key, color) in enumerate(LIGHTS):
        y = 34 + k * 56
        on = key == active
        parts.append(f'<circle cx="40" cy="{y}" r="22" fill="{color if on else "#1f2937"}" '
                     f'opacity="{1 if on else .55}" stroke="#0b1220" stroke-width="3" '
                     + (f'style="filter:drop-shadow(0 0 10px {color})"' if on else "") + "/>")
    return f'<svg viewBox="0 0 80 240" width="70">{"".join(parts)}</svg>'


def energy_blocks(bull_pct, n=10):
    """多空能量方塊：回傳 (多方方塊 HTML, 空方方塊 HTML)"""
    on = round(bull_pct / 100 * n)
    bull = "".join(f'<span style="background:{UP_COLOR if i < on else DIM}"></span>' for i in range(n))
    bear = "".join(f'<span style="background:{DOWN_COLOR if i < n - on else DIM}"></span>' for i in range(n))
    return bull, bear


def grade_of(avg):
    """雷達六項平均分數（0～10）→ 等級與說明"""
    if avg is None:
        return "—", "資料不足"
    if avg >= 7.5:
        return "A", "強勢股"
    if avg >= 6:
        return "B", "偏強"
    if avg >= 4.5:
        return "C", "中性"
    return "D", "偏弱"


def panel(no, title, body, sub="", cls=""):
    """一格面板：編號、標題、右上角小字、內容"""
    sub_html = f'<span class="psub">{sub}</span>' if sub else ""
    return (f'<div class="pnl {cls}"><div class="pt2"><span class="pno">{no}</span>{title}{sub_html}</div>'
            f'{body}</div>')
