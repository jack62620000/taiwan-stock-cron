import pandas as pd
import streamlit as st

# 台股習慣：紅漲綠跌
UP_COLOR   = "#dc2626"
DOWN_COLOR = "#16a34a"
WARN_COLOR = "#ea580c"

# 深藍儀表板配色（.streamlit/config.toml 也用同一組）
ACCENT     = "#fbbf24"  # 金色：標題、重點、主要線條
GLOW       = "#38bdf8"  # 青色：面板發光邊框、次要線條
BG_COLOR   = "#050b1a"
PANEL      = "#0b1631"
BORDER     = "#1e3a6e"
TEXT       = "#e2e8f0"
MUTED      = "#94a3b8"
SERIES     = ["#fbbf24", "#38bdf8", "#a78bfa", "#94a3b8"]  # 圖表多條線的顏色（避開紅綠，免得和漲跌混淆）

# 全站字型會蓋掉 Streamlit 內建圖示（展開箭頭、側邊欄收合鈕），所以 stIconMaterial 要換回圖示字型，
# 否則會顯示成 arrow_right、keyboard_double_arrow_left 之類的文字
_THEME_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=Noto+Sans+TC:wght@400;500;700;900&display=swap');
html, body, [class*="st-"], .stMarkdown, .stButton button, input, textarea, select {
    font-family: 'Noto Sans TC', 'IBM Plex Mono', sans-serif !important;
}
[data-testid="stIconMaterial"], [class*="material-symbols"] {
    font-family: 'Material Symbols Rounded' !important;
}
[data-testid="stApp"] {
    background:
        radial-gradient(ellipse at top, rgba(56,189,248,.08), transparent 60%),
        linear-gradient(rgba(56,189,248,.035) 1px, transparent 1px) 0 0 / 32px 32px,
        linear-gradient(90deg, rgba(56,189,248,.035) 1px, transparent 1px) 0 0 / 32px 32px,
        #050b1a;
}
[data-testid="stHeader"] { background: linear-gradient(90deg, #0b1631, #102a5c 50%, #0b1631);
                           border-bottom: 1px solid #38bdf8; box-shadow: 0 0 12px rgba(56,189,248,.35); height: 2.6rem; }
[data-testid="stHeader"]::before {
    content: "台股分析儀表板"; position: absolute; left: 1rem; top: 0.55rem;
    color: #fbbf24; font-weight: 900; letter-spacing: 3px; font-family: 'Noto Sans TC', sans-serif;
    text-shadow: 0 0 8px rgba(251,191,36,.45);
}
[data-testid="stHeader"] button, [data-testid="stHeader"] svg { color: #e2e8f0 !important; fill: #e2e8f0; }
h1 { color: #fbbf24 !important; font-weight: 900 !important; letter-spacing: 1px;
     text-shadow: 0 0 10px rgba(251,191,36,.35);
     border-bottom: 1px solid #1e3a6e; padding-bottom: 0.4rem !important; }
h2, h3 { color: #e2e8f0 !important; font-size: 1.05rem !important; letter-spacing: 1px;
         border-left: 3px solid #38bdf8; padding-left: 0.6rem !important;
         border-bottom: 1px solid #1e3a6e; padding-bottom: 0.3rem !important; }
[data-testid="stSidebar"] { background: #071026; border-right: 1px solid #1e3a6e; }
.stButton button, .stDownloadButton button {
    border-radius: 6px; border: 1px solid #38bdf8; color: #e2e8f0; background: rgba(56,189,248,.08);
}
.stButton button:hover, .stDownloadButton button:hover { background: #38bdf8; color: #050b1a; }
.stButton button[kind="primary"] { background: #fbbf24; border-color: #fbbf24; color: #050b1a; }
[data-testid="stAlert"] { background: #0b1631; border: 1px solid #1e3a6e; border-left: 3px solid #38bdf8; border-radius: 6px; }
[data-testid="stAlert"] * { color: #e2e8f0; }
[data-testid="stMetric"] { background: #0b1631; border: 1px solid #1e3a6e; padding: 10px 12px; border-radius: 8px;
                           box-shadow: 0 0 10px rgba(56,189,248,.15) inset; }
[data-testid="stExpander"] details { border: 1px solid #1e3a6e; border-radius: 8px; background: rgba(11,22,49,.6); }
[data-testid="stVerticalBlockBorderWrapper"] { border-color: #1e3a6e !important; border-radius: 8px; }
.stTabs [data-baseweb="tab"] { font-weight: 700; }
.stTabs [aria-selected="true"] { color: #fbbf24 !important; }
.stTabs [data-baseweb="tab-highlight"] { background-color: #fbbf24 !important; }
hr { border-color: #1e3a6e !important; }
</style>
"""

def apply_theme():
    """套用深藍儀表板風格（字體、背景格線、標題、按鈕、提示框等），每個頁面開頭呼叫一次"""
    st.markdown(_THEME_CSS, unsafe_allow_html=True)


# ===== 登入驗證 =====
def require_login():
    """未登入就導回首頁。每個 page 開頭呼叫一次。"""
    apply_theme()
    # 分組功能上線前登入的工作階段沒有記帳號，也請重新登入
    if not st.session_state.get("authenticated") or not st.session_state.get("user"):
        st.warning("請先登入")
        st.switch_page("main.py")
        st.stop()
    from utils.groups import render_account_sidebar
    render_account_sidebar()


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
    background: #0f2147;
    color: #fbbf24;
    padding: 8px 6px;
    text-align: center;
    border-bottom: 1px solid #38bdf8;
    position: sticky;
    top: 0;
    white-space: nowrap;
    z-index: 1;
    line-height: 1.4;
}}
.data-table td {{
    padding: 5px 8px;
    text-align: right;
    border-bottom: 1px solid #13254d;
    white-space: nowrap;
}}
{left_rules}
.data-table tr:hover {{ background: #102a5c; }}
.table-container {{
    max-height: 700px;
    overflow-y: auto;
    overflow-x: auto;
    border: 1px solid #1e3a6e;
    border-radius: 8px;
    background: #0b1631;
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
    border: 1px solid #1e3a6e;
    border-radius: 8px;
    padding: 12px 14px;
    background: linear-gradient(180deg, #0f1d3d, #0b1631);
    box-shadow: 0 0 10px rgba(56,189,248,.15) inset;
}
.metric-card .label { font-size: 12px; color: #94a3b8; letter-spacing: 1px; }
.metric-card .value { font-size: 24px; font-weight: 700; color: #e2e8f0; margin: 2px 0;
                      font-family: 'IBM Plex Mono', 'Noto Sans TC', monospace; }
.metric-card .delta { font-size: 13px; font-weight: 600; color: #94a3b8; }
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
