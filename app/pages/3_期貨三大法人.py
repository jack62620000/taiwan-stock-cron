import streamlit as st
import pandas as pd
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.db import supabase

st.set_page_config(
    page_title="期貨三大法人",
    page_icon="🔮",
    layout="wide",
    initial_sidebar_state="collapsed"
)
st.title("🔮 期貨三大法人未平倉")

if st.button("🔄 強制重新整理資料"):
    st.cache_data.clear()
    st.rerun()

# ===== 載入資料 =====
@st.cache_data(ttl=3600)
def load_futures_data():
    res = supabase.table("futures_institutional") \
        .select("*") \
        .eq("contract_code", "臺股期貨") \
        .order("date", desc=True) \
        .execute()
    return pd.DataFrame(res.data)

df = load_futures_data()

if df.empty:
    st.warning("無期貨三大法人資料")
    st.stop()

# ===== 篩選天數 =====
col1, col2 = st.columns([1, 3])
with col1:
    days = st.selectbox("顯示天數", [10, 20, 30, 60, 90], index=1)

all_dates = sorted(df["date"].unique(), reverse=True)[:days]
filtered = df[df["date"].isin(all_dates)].copy()

# ===== 法人類別對應 =====
item_map = {
    "外資": "外資",
    "外資及陸資": "外資及陸資",
    "投信": "投信",
    "自營商": "自營商",
}

available_items = [item for item in item_map.keys() if item in filtered["item"].unique()]

# ===== 各法人類別分頁 =====
tabs = st.tabs(available_items)

def color_value(val):
    """正數紅色、負數綠色"""
    if val is None or pd.isna(val):
        return ""
    if val > 0:
        return f'<span style="color:#dc2626;font-weight:600;">{val:,.0f}</span>'
    elif val < 0:
        return f'<span style="color:#16a34a;font-weight:600;">{val:,.0f}</span>'
    else:
        return f"{val:,.0f}"

table_style = """
<style>
.futures-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    font-family: -apple-system, "Noto Sans TC", sans-serif;
}
.futures-table th {
    background: #f0f2f6;
    padding: 8px 10px;
    text-align: center;
    border-bottom: 2px solid #ddd;
    white-space: nowrap;
}
.futures-table td {
    padding: 6px 10px;
    text-align: right;
    border-bottom: 1px solid #eee;
    white-space: nowrap;
}
.futures-table td:nth-child(1) {
    text-align: left;
    font-weight: 600;
}
.futures-table tr:hover {
    background: #f9f9f9;
}
</style>
"""
st.markdown(table_style, unsafe_allow_html=True)

for tab, item in zip(tabs, available_items):
    with tab:
        item_df = filtered[filtered["item"] == item].copy()
        item_df = item_df.sort_values("date", ascending=False)

        html_rows = []
        for _, row in item_df.iterrows():
            html_rows.append({
                "日期": row["date"],
                "多方未平倉口數": f"{row['long_oi']:,.0f}" if pd.notna(row.get("long_oi")) else "",
                "多方契約金額(千)": f"{row['long_oi_value']:,.0f}" if pd.notna(row.get("long_oi_value")) else "",
                "空方未平倉口數": f"{row['short_oi']:,.0f}" if pd.notna(row.get("short_oi")) else "",
                "空方契約金額(千)": f"{row['short_oi_value']:,.0f}" if pd.notna(row.get("short_oi_value")) else "",
                "淨未平倉口數": color_value(row.get("net_oi")),
                "淨契約金額(千)": color_value(row.get("net_oi_value")),
            })

        html_df = pd.DataFrame(html_rows)
        table_html = html_df.to_html(escape=False, index=False, classes="futures-table")
        st.markdown(table_html, unsafe_allow_html=True)

        # 下載按鈕
        csv = item_df[["date", "long_oi", "long_oi_value", "short_oi", "short_oi_value", "net_oi", "net_oi_value"]] \
            .to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            label=f"📥 下載{item}資料 CSV",
            data=csv,
            file_name=f"期貨三大法人_{item}.csv",
            mime="text/csv",
            key=f"download_{item}"
        )