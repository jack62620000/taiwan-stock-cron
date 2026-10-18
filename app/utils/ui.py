import pandas as pd
import streamlit as st

# 台股習慣：紅漲綠跌
UP_COLOR   = "#dc2626"
DOWN_COLOR = "#16a34a"
WARN_COLOR = "#ea580c"


# ===== 登入驗證 =====
def require_login():
    """未登入就導回首頁。每個 page 開頭呼叫一次。"""
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
    font-family: -apple-system, "Noto Sans TC", sans-serif;
}}
.data-table th {{
    background: #f0f2f6;
    padding: 8px 6px;
    text-align: center;
    border-bottom: 2px solid #ddd;
    position: sticky;
    top: 0;
    white-space: nowrap;
    z-index: 1;
    line-height: 1.4;
}}
.data-table td {{
    padding: 5px 8px;
    text-align: right;
    border-bottom: 1px solid #eee;
    white-space: nowrap;
}}
{left_rules}
.data-table tr:hover {{ background: #f9f9f9; }}
.table-container {{
    max-height: 700px;
    overflow-y: auto;
    overflow-x: auto;
    border: 1px solid #ddd;
    border-radius: 6px;
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
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    padding: 12px 14px;
    background: #ffffff;
}
.metric-card .label { font-size: 13px; color: #4b5563; }
.metric-card .value { font-size: 24px; font-weight: 700; color: #111827; margin: 2px 0; }
.metric-card .delta { font-size: 13px; font-weight: 600; color: #4b5563; }
</style>
"""

def render_metric_cards(cards):
    """
    一排指標卡片（台股配色：漲紅跌綠）。
    cards 是 [(標題, 數值文字, 變化文字, 正負號), ...]；正負號 >0 紅色、<0 綠色、0 或 None 灰色。
    """
    html = []
    for label, value, delta, sign in cards:
        color = UP_COLOR if sign and sign > 0 else DOWN_COLOR if sign and sign < 0 else "#4b5563"
        html.append(
            f'<div class="metric-card"><div class="label">{label}</div>'
            f'<div class="value">{value}</div>'
            f'<div class="delta" style="color:{color};">{delta or "&nbsp;"}</div></div>'
        )
    st.markdown(_METRIC_CSS + f'<div class="metric-row">{"".join(html)}</div>', unsafe_allow_html=True)
