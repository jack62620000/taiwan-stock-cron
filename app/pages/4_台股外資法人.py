import streamlit as st
import pandas as pd
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.db import get_stock_list, get_recent_dates, get_snapshots
from utils.ui import require_login, color_value, render_html_table
require_login()

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

    data = get_snapshots("stock_ins_daily", dates_found, "stock_id, date, foreign_net_shares")
    return data, dates_found

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

render_html_table(pd.DataFrame(html_rows), left_cols=2)

# ===== 下載按鈕 =====
st.markdown("---")
csv = filtered.to_csv(index=False, encoding="utf-8-sig")
st.download_button(
    label="📥 下載資料 CSV",
    data=csv,
    file_name="每日三大法人買賣超.csv",
    mime="text/csv",
)
