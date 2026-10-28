import streamlit as st
import pandas as pd
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.glossary import render_page_guide
from utils.db import get_futures_ins
from utils.ui import require_login, style_sign
require_login()

st.set_page_config(
    page_title="期貨法人",
    page_icon="🔮",
    layout="wide",
    initial_sidebar_state="collapsed"
)
st.title("🔮 期貨三大法人未平倉")
render_page_guide("futures")

if st.button("🔄 強制重新整理資料"):
    st.cache_data.clear()
    st.rerun()

# ===== 載入資料 =====
@st.cache_data(ttl=3600)
def load_futures_data():
    return get_futures_ins()

df = load_futures_data()

if df.empty:
    st.warning("無期貨三大法人資料")
    st.stop()

# ===== 篩選天數 =====
col1, col2 = st.columns([1, 3])
with col1:
    days = st.selectbox("顯示天數", [10, 20, 30, 60, 90, 180, 365], index=1)

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

for tab, item in zip(tabs, available_items):
    with tab:
        item_df = filtered[filtered["item"] == item].copy()
        item_df = item_df.sort_values("date", ascending=False)

        cols = {"date": "日期", "long_oi": "多方未平倉口數", "long_oi_value": "多方契約金額(千)",
                "short_oi": "空方未平倉口數", "short_oi_value": "空方契約金額(千)",
                "net_oi": "淨未平倉口數", "net_oi_value": "淨契約金額(千)"}
        table = item_df[list(cols)].rename(columns=cols)
        nums = list(cols.values())[1:]
        table[nums] = table[nums].apply(pd.to_numeric, errors="coerce")
        st.dataframe(
            table.style.format("{:,.0f}", subset=nums, na_rep="")
                 .map(style_sign, subset=["淨未平倉口數", "淨契約金額(千)"]),
            hide_index=True, width="stretch", height=min(600, 38 + 35 * len(table)),
            column_config={"日期": st.column_config.TextColumn(pinned=True)},
        )

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
