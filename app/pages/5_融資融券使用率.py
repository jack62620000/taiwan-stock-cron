import streamlit as st
import pandas as pd
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.db import get_stock_list, get_recent_dates, get_snapshots
from utils.ui import require_login, color_rate, fmt_number, render_html_table
require_login()

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
    # 一次查出最近 N 個交易日（走主鍵索引，不必一天一天往回找）
    dates_found = get_recent_dates("margin_utilization", n=num_days)
    if not dates_found:
        return pd.DataFrame(), []

    data = get_snapshots(
        "margin_utilization", dates_found,
        "stock_id, date, margin_balance, margin_limit, margin_rate",
    )
    return data, dates_found

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

# ===== 建立 HTML 表格 =====
html_rows = []
for _, row in filtered.iterrows():
    record = {
        "股票代號": row["stock_id"],
        "公司名稱": row["stock_name"],
        "市場":     row.get("market", ""),
        "融資限額": fmt_number(row.get("融資限額"), blank_zero=True),
    }
    for d in dates_found:
        record[f"融資餘額\n{d}"]  = fmt_number(row.get(f"餘額_{d}"), blank_zero=True)
        record[f"融資使用率\n{d}"] = color_rate(row.get(f"使用率_{d}"))
    html_rows.append(record)

render_html_table(pd.DataFrame(html_rows), left_cols=3)

# ===== 下載按鈕 =====
st.markdown("---")
csv = filtered.to_csv(index=False, encoding="utf-8-sig")
st.download_button(
    label="📥 下載融資使用率 CSV",
    data=csv,
    file_name=f"融資使用率_{latest_date}.csv",
    mime="text/csv",
)
