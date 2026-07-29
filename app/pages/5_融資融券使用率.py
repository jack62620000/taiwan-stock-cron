import streamlit as st
import pandas as pd
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.db import get_stock_list, supabase

st.set_page_config(
    page_title="融資使用率",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="collapsed"
)
st.title("💰 全台股融資使用率")

if st.button("🔄 強制重新整理資料"):
    st.cache_data.clear()
    st.rerun()

# ===== 載入資料 =====
@st.cache_data(ttl=3600)
def load_margin_util_data(num_days=6):
    """抓取最近 N 個交易日的融資使用率資料"""
    res = supabase.table("margin_utilization") \
        .select("date") \
        .order("date", desc=True) \
        .limit(1) \
        .execute()
    if not res.data:
        return pd.DataFrame(), []

    latest_date = res.data[0]["date"]

    # 往前找 N 個不同的交易日
    dates_found = []
    current_date = latest_date
    max_try = 30
    tried = 0
    while len(dates_found) < num_days and tried < max_try:
        check = supabase.table("margin_utilization").select("date") \
            .eq("date", current_date).limit(1).execute()
        if check.data:
            dates_found.append(current_date)
        prev_res = supabase.table("margin_utilization").select("date") \
            .lt("date", current_date).order("date", desc=True).limit(1).execute()
        if not prev_res.data:
            break
        current_date = prev_res.data[0]["date"]
        tried += 1

    # 抓這些日期的所有資料
    all_data = []
    for d in dates_found:
        start = 0
        while True:
            r = supabase.table("margin_utilization") \
                .select("stock_id, date, margin_balance, margin_limit, margin_rate") \
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
util_data, dates_found = load_margin_util_data(num_days=6)

if util_data.empty:
    st.warning("無融資使用率資料")
    st.stop()

st.info(f"資料區間：{dates_found[-1]} ～ {dates_found[0]}（共 {len(dates_found)} 個交易日）")

# ===== 搜尋篩選 =====
col1, col2, col3 = st.columns(3)
with col1:
    search_id = st.text_input("股票代號（可輸入多個，用逗號分隔）", placeholder="例如: 2330,2454")
with col2:
    search_name = st.text_input("公司名稱關鍵字", placeholder="例如: 台積")
with col3:
    markets = ["全部", "上市", "上櫃"]
    selected_market = st.selectbox("市場", markets)

st.sidebar.header("🔍 篩選條件")
margin_rate_min = st.sidebar.number_input("融資使用率下限 (%)", value=0.0, step=1.0)
margin_rate_max = st.sidebar.number_input("融資使用率上限 (%)", value=100.0, step=1.0)

# ===== 整理成寬表格 =====
# 以股票清單為底
base = stock_list[["stock_id", "stock_name", "market"]].copy()

# 取最新一天的融資限額（固定欄位）
latest_date = dates_found[0]
latest_data = util_data[util_data["date"] == latest_date][
    ["stock_id", "margin_limit"]
].copy()
latest_data.columns = ["stock_id", "融資限額"]
base = base.merge(latest_data, on="stock_id", how="left")

# 依次合併每一天的融資餘額和使用率
for d in dates_found:
    day_data = util_data[util_data["date"] == d][
        ["stock_id", "margin_balance", "margin_rate"]
    ].copy()
    day_data.columns = ["stock_id", f"餘額_{d}", f"使用率_{d}"]
    base = base.merge(day_data, on="stock_id", how="left")

# ===== 套用篩選 =====
filtered = base.copy()

if search_id:
    ids = [s.strip() for s in search_id.split(",") if s.strip()]
    filtered = filtered[filtered["stock_id"].isin(ids)]

if search_name:
    filtered = filtered[filtered["stock_name"].str.contains(search_name, na=False)]

if selected_market != "全部":
    filtered = filtered[filtered["market"] == selected_market]

# 依最新日使用率篩選
latest_rate_col = f"使用率_{dates_found[0]}"
if latest_rate_col in filtered.columns:
    filtered = filtered[
        filtered[latest_rate_col].isna() |
        (
            (filtered[latest_rate_col] >= margin_rate_min) &
            (filtered[latest_rate_col] <= margin_rate_max)
        )
    ]

# 依按股票代號排序
filtered = filtered.sort_values("stock_id", ascending=True)

filtered = filtered.reset_index(drop=True)

st.metric("顯示股票數", f"{len(filtered):,} 檔")
st.markdown("---")

# ===== 顏色函式 =====
def color_rate(val):
    """融資使用率上色：>20% 紅色，>10% 橘色"""
    if val is None or pd.isna(val):
        return ""
    if val >= 20:
        return f'<span style="color:#dc2626;font-weight:700;">{val:.2f}%</span>'
    elif val >= 10:
        return f'<span style="color:#ea580c;font-weight:600;">{val:.2f}%</span>'
    else:
        return f"{val:.2f}%"

def fmt_shares(val):
    if val is None or pd.isna(val) or val == 0:
        return ""
    return f"{val:,.0f}"

# ===== 建立 HTML 表格 =====
html_rows = []
for _, row in filtered.iterrows():
    record = {
        "股票代號": row["stock_id"],
        "公司名稱": row["stock_name"],
        "市場":     row.get("market", ""),
        "融資限額": fmt_shares(row.get("融資限額")),
    }
    for d in dates_found:
        record[f"融資餘額\n{d}"]  = fmt_shares(row.get(f"餘額_{d}"))
        record[f"融資使用率\n{d}"] = color_rate(row.get(f"使用率_{d}"))
    html_rows.append(record)

html_df = pd.DataFrame(html_rows)

# 表頭換行顯示
display_cols = {col: col.replace("\n", "<br>") for col in html_df.columns}
html_df_display = html_df.rename(columns=display_cols)

table_html = html_df_display.to_html(escape=False, index=False, classes="margin-table")

table_style = """
<style>
.margin-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    font-family: -apple-system, "Noto Sans TC", sans-serif;
}
.margin-table th {
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
.margin-table td {
    padding: 5px 8px;
    text-align: right;
    border-bottom: 1px solid #eee;
    white-space: nowrap;
}
.margin-table td:nth-child(1),
.margin-table td:nth-child(2),
.margin-table td:nth-child(3) {
    text-align: left;
}
.margin-table tr:hover { background: #f9f9f9; }
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
    label="📥 下載融資使用率 CSV",
    data=csv,
    file_name=f"融資使用率_{latest_date}.csv",
    mime="text/csv",
)