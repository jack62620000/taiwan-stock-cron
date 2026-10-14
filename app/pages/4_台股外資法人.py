import streamlit as st
import pandas as pd
import sys
import os

# ===== 登入驗證 =====
if "authenticated" not in st.session_state or not st.session_state["authenticated"]:
    st.warning("請先登入")
    st.switch_page("main.py")
    st.stop()
# ===== 主程式碼 ====
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.db import get_stock_list, get_recent_dates, supabase

st.set_page_config(
    page_title="台股外資法人",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="collapsed"
)
st.title("🏦 全台股外資買賣超")

if st.button("🔄 強制重新整理資料"):
    st.cache_data.clear()
    st.rerun()

# ===== 載入資料 =====
@st.cache_data(ttl=3600)
def load_recent_ins_data(num_days=5):
    """抓取最近N個交易日的三大法人資料"""
    # 一次查出最近 N 個交易日（走主鍵索引，不必一天一天往回找）
    dates_found = get_recent_dates("stock_ins_daily", n=num_days)
    if not dates_found:
        return pd.DataFrame(), []

    # 抓這些日期的所有資料
    all_data = []
    for d in dates_found:
        start = 0
        while True:
            r = supabase.table("stock_ins_daily") \
                .select("stock_id, date, foreign_net_shares") \
                .eq("date", d) \
                .order("stock_id", desc=False) \
                .range(start, start + 999) \
                .execute()
            if not r.data:
                break
            all_data.extend(r.data)
            if len(r.data) < 1000:
                break
            start += 1000

    return pd.DataFrame(all_data), dates_found

stock_list = get_stock_list()
ins_data, dates_found = load_recent_ins_data(num_days=15)

if ins_data.empty:
    st.warning("無三大法人資料")
    st.stop()

st.info(f"資料區間：{dates_found[-1]} ～ {dates_found[0]}（共 {len(dates_found)} 個交易日）")

# ===== 搜尋篩選 =====
col1, col2 = st.columns(2)
with col1:
    search_id = st.text_input("股票代號（可輸入多個，用逗號分隔）", placeholder="例如: 2330,2454")
with col2:
    search_name = st.text_input("公司名稱關鍵字", placeholder="例如: 台積")

st.markdown("### 🔍 連續買賣超篩選")
col3, col4 = st.columns(2)
with col3:
    trend_type = st.selectbox("篩選類型", ["不篩選", "連續買超", "連續賣超"])
with col4:
    max_days = len(dates_found)
    trend_days = st.number_input(
        f"連續天數（最多 {max_days} 天）",
        min_value=1, max_value=max_days, value=min(3, max_days), step=1
    )

# ===== 整理成寬表格（pivot）=====
def color_value(val):
    if val is None or pd.isna(val):
        return ""
    if val > 0:
        return f'<span style="color:#dc2626;font-weight:600;">{val:,.0f}</span>'
    elif val < 0:
        return f'<span style="color:#16a34a;font-weight:600;">{val:,.0f}</span>'
    else:
        return f"{val:,.0f}"

# 以股票清單為底，依次合併每一天的資料
base = stock_list[["stock_id", "stock_name"]].copy()

for d in dates_found:
    day_data = ins_data[ins_data["date"] == d][
        ["stock_id", "foreign_net_shares"]
    ].copy()
    day_data.columns = ["stock_id", f"外資_{d}"]
    base = base.merge(day_data, on="stock_id", how="left")

# ===== 套用篩選 =====
filtered = base.copy()
if search_id:
    ids = [s.strip() for s in search_id.split(",") if s.strip()]
    filtered = filtered[filtered["stock_id"].isin(ids)]
if search_name:
    filtered = filtered[filtered["stock_name"].str.contains(search_name, na=False)]

# 連續買超/賣超篩選（從最新日期往前抓N天）
if trend_type != "不篩選":
    check_dates = dates_found[:trend_days]  # 最新的N天
    cols_to_check = [f"外資_{d}" for d in check_dates]

    def check_trend(row):
        values = [row.get(c) for c in cols_to_check]
        if any(pd.isna(v) for v in values):
            return False
        if trend_type == "連續買超":
            return all(v > 0 for v in values)
        else:
            return all(v < 0 for v in values)

    filtered = filtered[filtered.apply(check_trend, axis=1)]

filtered = filtered.sort_values("stock_id").reset_index(drop=True)

st.metric("顯示股票數", f"{len(filtered):,} 檔")
st.markdown("---")

# ===== 建立 HTML 表格 =====
html_rows = []
for _, row in filtered.iterrows():
    record = {
        "股票代號": row["stock_id"],
        "公司名稱": row["stock_name"],
    }
    for d in dates_found:
        record[f"外資\n{d}"] = color_value(row.get(f"外資_{d}"))
    html_rows.append(record)

html_df = pd.DataFrame(html_rows)

# 表頭用換行顯示日期
display_cols = {}
for col in html_df.columns:
    display_cols[col] = col.replace("\n", "<br>")
html_df_display = html_df.rename(columns=display_cols)

table_html = html_df_display.to_html(escape=False, index=False, classes="ins-table")

table_style = """
<style>
.ins-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    font-family: -apple-system, "Noto Sans TC", sans-serif;
}
.ins-table th {
    background: #f0f2f6;
    padding: 8px 6px;
    text-align: center;
    border-bottom: 2px solid #ddd;
    position: sticky;
    top: 0;
    white-space: nowrap;
    z-index: 1;
    line-height: 1.4;
}
.ins-table td {
    padding: 5px 8px;
    text-align: right;
    border-bottom: 1px solid #eee;
    white-space: nowrap;
}
.ins-table td:nth-child(1), .ins-table td:nth-child(2) {
    text-align: left;
}
.ins-table tr:hover {
    background: #f9f9f9;
}
.table-container {
    max-height: 700px;
    overflow-y: auto;
    overflow-x: auto;
    border: 1px solid #ddd;
    border-radius: 6px;
}
</style>
"""

st.markdown(table_style, unsafe_allow_html=True)
st.markdown(f'<div class="table-container">{table_html}</div>', unsafe_allow_html=True)

# ===== 下載按鈕 =====
st.markdown("---")
csv = filtered.to_csv(index=False, encoding="utf-8-sig")
st.download_button(
    label="📥 下載資料 CSV",
    data=csv,
    file_name="每日三大法人買賣超.csv",
    mime="text/csv",
)
