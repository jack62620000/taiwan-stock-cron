import pandas as pd
import streamlit as st

# 台股習慣：紅漲綠跌
UP_COLOR   = "#dc2626"
DOWN_COLOR = "#16a34a"
WARN_COLOR = "#ea580c"

# 金融終端配色（.streamlit/config.toml 也用同一組）
ACCENT     = "#f5a524"  # 琥珀色：標題、重點、主要線條
BG_COLOR   = "#0a0a0a"
PANEL      = "#141414"
BORDER     = "#2a2a2a"
TEXT       = "#e5e5e5"
MUTED      = "#a3a3a3"
SERIES     = ["#f5a524", "#60a5fa", "#a78bfa", "#9ca3af"]  # 圖表多條線的顏色（避開紅綠，免得和漲跌混淆）

_THEME_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=Noto+Sans+TC:wght@400;500;700&display=swap');
html, body, [class*="st-"], .stMarkdown, .stButton button, input, textarea, select {
    font-family: 'IBM Plex Mono', 'Noto Sans TC', monospace !important;
}
[data-testid="stHeader"] { background: #f5a524; height: 2.6rem; }
[data-testid="stHeader"]::before {
    content: "TWSE TERMINAL"; position: absolute; left: 1rem; top: 0.6rem;
    color: #0a0a0a; font-weight: 700; letter-spacing: 2px; font-family: 'IBM Plex Mono', monospace;
}
[data-testid="stHeader"] button, [data-testid="stHeader"] svg { color: #0a0a0a !important; fill: #0a0a0a; }
h1 { color: #f5a524 !important; font-weight: 700 !important; letter-spacing: 1px;
     border-bottom: 1px solid #2a2a2a; padding-bottom: 0.4rem !important; }
h2, h3 { color: #f5a524 !important; font-size: 1.05rem !important; letter-spacing: 1px;
         border-bottom: 1px solid #2a2a2a; padding-bottom: 0.3rem !important; }
[data-testid="stSidebar"] { background: #111111; border-right: 1px solid #2a2a2a; }
.stButton button, .stDownloadButton button {
    border-radius: 2px; border: 1px solid #f5a524; color: #f5a524; background: transparent;
}
.stButton button:hover, .stDownloadButton button:hover { background: #f5a524; color: #0a0a0a; }
.stButton button[kind="primary"] { background: #f5a524; color: #0a0a0a; }
[data-testid="stAlert"] { background: #141414; border: 1px solid #2a2a2a; border-left: 3px solid #f5a524; border-radius: 2px; }
[data-testid="stAlert"] * { color: #e5e5e5; }
[data-testid="stMetric"] { background: #141414; border: 1px solid #2a2a2a; padding: 10px 12px; border-radius: 2px; }
[data-testid="stExpander"] details { border: 1px solid #2a2a2a; border-radius: 2px; }
.stTabs [data-baseweb="tab"] { font-weight: 600; }
.stTabs [aria-selected="true"] { color: #f5a524 !important; }
hr { border-color: #2a2a2a !important; }
</style>
"""

def apply_theme():
    """套用金融終端風格（字體、標題、按鈕、提示框等），每個頁面開頭呼叫一次"""
    st.markdown(_THEME_CSS, unsafe_allow_html=True)


# ===== 登入驗證 =====
def require_login():
    """未登入就導回首頁。每個 page 開頭呼叫一次。"""
    apply_theme()
    if not st.session_state.get("authenticated"):
        st.warning("請先登入")
        st.switch_page("main.py")
        st.stop()


# ===== Styler 用（st.dataframe）=====
def style_sign(val):
    """正數紅色、負數綠色"""
    if val is None or pd.isna(val):
        return ""
    if val > 0:
        return f"color:{UP_COLOR};font-weight:600;"
    if val < 0:
        return f"color:{DOWN_COLOR};font-weight:600;"
    return ""

def style_rate(val):
    """融資使用率上色：>=20% 紅色，>=10% 橘色，其餘正常"""
    if val is None or pd.isna(val):
        return ""
    if val >= 20:
        return f"color:{UP_COLOR};font-weight:700;"
    if val >= 10:
        return f"color:{WARN_COLOR};font-weight:600;"
    return ""


# ===== HTML 表格用 =====
def color_value(val, decimals=0):
    """數字加千分位，正數紅色、負數綠色"""
    if val is None or pd.isna(val):
        return ""
    text = f"{val:,.{decimals}f}"
    style = style_sign(val)
    return f'<span style="{style}">{text}</span>' if style else text

def color_rate(val):
    """百分比，依融資使用率門檻上色"""
    if val is None or pd.isna(val):
        return ""
    text = f"{val:.2f}%"
    style = style_rate(val)
    return f'<span style="{style}">{text}</span>' if style else text

def fmt_number(val, decimals=0, blank_zero=False):
    """數字加千分位；空值（或 blank_zero 時的 0）顯示空白"""
    if val is None or pd.isna(val) or (blank_zero and val == 0):
        return ""
    return f"{val:,.{decimals}f}"


_TABLE_CSS = """
<style>
.data-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: {font_size}px;
    font-family: 'IBM Plex Mono', 'Noto Sans TC', monospace;
}}
.data-table th {{
    background: #1c1c1c;
    color: #f5a524;
    padding: 8px 6px;
    text-align: center;
    border-bottom: 1px solid #f5a524;
    position: sticky;
    top: 0;
    white-space: nowrap;
    z-index: 1;
    line-height: 1.4;
}}
.data-table td {{
    padding: 5px 8px;
    text-align: right;
    border-bottom: 1px solid #1f1f1f;
    white-space: nowrap;
}}
{left_rules}
.data-table tr:hover {{ background: #1c1c1c; }}
.table-container {{
    max-height: 700px;
    overflow-y: auto;
    overflow-x: auto;
    border: 1px solid #2a2a2a;
    border-radius: 2px;
}}
</style>
"""

def render_html_table(df, left_cols=1, font_size=12):
    """
    把已格式化（可含 HTML 上色）的 DataFrame 畫成可捲動表格。
    前 left_cols 欄靠左（代號、名稱等文字欄），其餘靠右；欄名裡的換行會換成 <br>。
    """
    left_rules = "\n".join(
        f".data-table td:nth-child({i}) {{ text-align: left; }}" for i in range(1, left_cols + 1)
    )
    display = df.rename(columns={c: str(c).replace("\n", "<br>") for c in df.columns})
    table_html = display.to_html(escape=False, index=False, classes="data-table")
    st.markdown(_TABLE_CSS.format(font_size=font_size, left_rules=left_rules), unsafe_allow_html=True)
    st.markdown(f'<div class="table-container">{table_html}</div>', unsafe_allow_html=True)


# ===== 指標卡片 =====
_METRIC_CSS = """
<style>
.metric-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
    gap: 12px;
    margin-bottom: 8px;
}
.metric-card {
    border: 1px solid #2a2a2a;
    border-radius: 2px;
    padding: 12px 14px;
    background: #141414;
}
.metric-card .label { font-size: 12px; color: #a3a3a3; letter-spacing: 1px; }
.metric-card .value { font-size: 24px; font-weight: 700; color: #e5e5e5; margin: 2px 0; }
.metric-card .delta { font-size: 13px; font-weight: 600; color: #a3a3a3; }
</style>
"""

def render_metric_cards(cards):
    """
    一排指標卡片（台股配色：漲紅跌綠）。
    cards 是 [(標題, 數值文字, 變化文字, 正負號), ...]；正負號 >0 紅色、<0 綠色、0 或 None 灰色。
    """
    html = []
    for label, value, delta, sign in cards:
        color = UP_COLOR if sign and sign > 0 else DOWN_COLOR if sign and sign < 0 else MUTED
        html.append(
            f'<div class="metric-card"><div class="label">{label}</div>'
            f'<div class="value">{value}</div>'
            f'<div class="delta" style="color:{color};">{delta or "&nbsp;"}</div></div>'
        )
    st.markdown(_METRIC_CSS + f'<div class="metric-row">{"".join(html)}</div>', unsafe_allow_html=True)
