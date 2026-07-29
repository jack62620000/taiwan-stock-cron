import streamlit as st
import pandas as pd
import numpy as np
import sys
import os

# ===== 登入驗證 =====
if "authenticated" not in st.session_state or not st.session_state["authenticated"]:
    st.warning("請先登入")
    st.switch_page("main.py")
    st.stop()
# ===== 主程式碼 =====
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.db import (
    get_stock_list, get_latest_prices, get_latest_ins,
    get_latest_margin, get_latest_holder, get_latest_revenue,
    get_latest_quarterly, get_latest_margin_utilization
)
from utils.indicators import calc_all, get_latest_signals
from utils.db import get_daily_prices

st.set_page_config(
    page_title="全台股總覽",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed"
)
st.title("📊 全台股總覽")

if st.button("🔄 強制重新整理資料"):
    st.cache_data.clear()
    st.rerun()

# ===== 載入資料 =====
@st.cache_data(ttl=3600)
def load_overview_data():
    with st.spinner("正在載入資料..."):
        stock_list        = get_stock_list()
        prices            = get_latest_prices()
        ins               = get_latest_ins()
        margin            = get_latest_margin()
        margin_util       = get_latest_margin_utilization()  # ✅ 新增
        holder            = get_latest_holder()
        revenue           = get_latest_revenue()
        quarterly         = get_latest_quarterly()
    return stock_list, prices, ins, margin, margin_util, holder, revenue, quarterly

stock_list, prices, ins, margin, margin_util, holder, revenue, quarterly = load_overview_data()

# ===== 合併資料 =====
df = stock_list.copy()

# 合併股價
if not prices.empty:
    prices_latest = prices.drop_duplicates(subset="stock_id", keep="first")
    df = df.merge(
        prices_latest[["stock_id", "date", "open_price", "close_price", "volume"]],
        on="stock_id", how="left"
    )
    df.rename(columns={"date": "price_date"}, inplace=True)

# 合併三大法人
if not ins.empty:
    latest_ins_date = ins["date"].max()
    ins_latest = ins[ins["date"] == latest_ins_date].copy()
    ins_latest = ins_latest.drop_duplicates(subset="stock_id", keep="first")
    df = df.merge(
        ins_latest[["stock_id", "foreign_net_shares", "sitca_net_shares",
                    "dealers_net_shares", "total_net_shares"]],
        on="stock_id", how="left"
    )

# 合併融資融券餘額
if not margin.empty:
    latest_margin_date = margin["date"].max()
    margin_latest = margin[margin["date"] == latest_margin_date].copy()
    margin_latest = margin_latest.drop_duplicates(subset="stock_id", keep="first")
    df = df.merge(
        margin_latest[["stock_id", "margin_balance", "short_balance"]],
        on="stock_id", how="left"
    )

# ✅ 合併融資融券使用率
if not margin_util.empty:
    latest_util_date = margin_util["date"].max()
    util_latest = margin_util[margin_util["date"] == latest_util_date].copy()
    util_latest = util_latest.drop_duplicates(subset="stock_id", keep="first")
    df = df.merge(
        util_latest[["stock_id", "margin_rate", "short_rate"]],
        on="stock_id", how="left"
    )

# 合併大戶持股
if not holder.empty:
    latest_holder_date = holder["week_date"].max()
    holder_latest = holder[holder["week_date"] == latest_holder_date].copy()
    holder_latest = holder_latest.drop_duplicates(subset="stock_id", keep="first")
    df = df.merge(
        holder_latest[["stock_id", "big_holder_ratio"]],
        on="stock_id", how="left"
    )

# 合併月營收
if not revenue.empty:
    latest_ym = revenue["year_month"].max()
    rev_latest = revenue[revenue["year_month"] == latest_ym].copy()
    rev_latest = rev_latest.drop_duplicates(subset="stock_id", keep="first")
    df = df.merge(
        rev_latest[["stock_id", "year_month", "revenue", "yoy_growth", "mom_growth"]],
        on="stock_id", how="left"
    )

# 合併季報
if not quarterly.empty:
    q_latest = quarterly.sort_values(["year", "quarter"], ascending=False) \
        .drop_duplicates(subset="stock_id", keep="first")
    df = df.merge(
        q_latest[["stock_id", "year", "quarter", "eps",
                  "book_value", "pb_ratio", "roe", "net_income"]],
        on="stock_id", how="left"
    )

# 計算本益比
if "eps" in df.columns and "close_price" in df.columns:
    def calc_pe(row):
        eps = row.get("eps")
        close = row.get("close_price")
        if pd.isna(eps):
            return None
        if eps <= 0:
            return "虧損"
        if pd.isna(close):
            return None
        return round(close / (eps * 4), 2)
    df["pe_ratio"] = df.apply(calc_pe, axis=1)

# 計算漲跌
if "open_price" in df.columns and "close_price" in df.columns:
    df["price_change"] = df["close_price"] - df["open_price"]

# ===== 側邊篩選器 =====
st.sidebar.header("🔍 篩選條件")
st.sidebar.markdown("### 🔍 搜尋")
search_id   = st.sidebar.text_input("股票代號（可輸入多個，用逗號分隔）", placeholder="例如: 2330,2454,0050")
search_name = st.sidebar.text_input("公司名稱關鍵字", placeholder="例如: 台積")

markets = ["全部"] + sorted(df["market"].dropna().unique().tolist())
selected_market = st.sidebar.selectbox("市場", markets)

industries = ["全部"] + sorted(df["industry"].dropna().unique().tolist())
selected_industry = st.sidebar.selectbox("產業別", industries)

st.sidebar.markdown("### 價值面")
pb_max  = st.sidebar.number_input("P/B 上限", value=999.0, step=0.5)
roe_min = st.sidebar.number_input("ROE 下限 (%)", value=-999.0, step=1.0)

st.sidebar.markdown("### 成長面")
yoy_min = st.sidebar.number_input("月營收年增率下限 (%)", value=-999.0, step=5.0)

st.sidebar.markdown("### 籌碼面")
foreign_min = st.sidebar.number_input("外資買超下限（股）", value=-999999999, step=1000)

st.sidebar.markdown("### 技術面")
rsi_min = st.sidebar.number_input("RSI 下限", value=0.0, step=5.0)
rsi_max = st.sidebar.number_input("RSI 上限", value=100.0, step=5.0)

# ===== 套用篩選 =====
filtered = df.copy()

if search_id:
    ids = [s.strip() for s in search_id.split(",") if s.strip()]
    filtered = filtered[filtered["stock_id"].isin(ids)]

if search_name:
    filtered = filtered[filtered["stock_name"].str.contains(search_name, na=False)]

if selected_market != "全部":
    filtered = filtered[filtered["market"] == selected_market]

if selected_industry != "全部":
    filtered = filtered[filtered["industry"] == selected_industry]

if "pb_ratio" in filtered.columns:
    filtered = filtered[filtered["pb_ratio"].isna() | (filtered["pb_ratio"] <= pb_max)]

if "roe" in filtered.columns:
    filtered = filtered[filtered["roe"].isna() | (filtered["roe"] >= roe_min)]

if "yoy_growth" in filtered.columns:
    filtered = filtered[filtered["yoy_growth"].isna() | (filtered["yoy_growth"] >= yoy_min)]

if "foreign_net_shares" in filtered.columns:
    filtered = filtered[filtered["foreign_net_shares"].isna() | (filtered["foreign_net_shares"] >= foreign_min)]

# ===== 顯示統計 =====
col1, col2, col3, col4 = st.columns(4)
col1.metric("篩選後股票數", f"{len(filtered):,} 檔")
col2.metric("上市", f"{len(filtered[filtered['market']=='上市']):,} 檔")
col3.metric("上櫃", f"{len(filtered[filtered['market']=='上櫃']):,} 檔")
col4.metric("資料日期", prices["date"].max() if not prices.empty else "N/A")

st.markdown("---")

# ===== 顯示資料表 =====
st.subheader("📋 全台股數據總覽")

def color_price(val, price_change):
    if val is None or pd.isna(val):
        return ""
    if price_change is None or pd.isna(price_change):
        return f"{val:,.2f}"
    if price_change > 0:
        return f'<span style="color:#dc2626;font-weight:600;">{val:,.2f}</span>'
    elif price_change < 0:
        return f'<span style="color:#16a34a;font-weight:600;">{val:,.2f}</span>'
    else:
        return f"{val:,.2f}"

def color_value(val):
    if val is None or pd.isna(val):
        return ""
    if val > 0:
        return f'<span style="color:#dc2626;font-weight:600;">{val:,.2f}</span>'
    elif val < 0:
        return f'<span style="color:#16a34a;font-weight:600;">{val:,.2f}</span>'
    else:
        return f"{val:,.2f}"

def color_int(val):
    if val is None or pd.isna(val):
        return ""
    if val > 0:
        return f'<span style="color:#dc2626;font-weight:600;">{val:,.0f}</span>'
    elif val < 0:
        return f'<span style="color:#16a34a;font-weight:600;">{val:,.0f}</span>'
    else:
        return f"{val:,.0f}"

def fmt_pe(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    if val == "虧損":
        return '<span style="color:#16a34a;font-weight:600;">虧損</span>'
    return f"{val:,.2f}"

def color_rate(val):
    """融資使用率上色：>20% 紅色，>10% 橘色，其餘正常"""
    if val is None or pd.isna(val):
        return ""
    if val >= 20:
        return f'<span style="color:#dc2626;font-weight:700;">{val:.2f}%</span>'
    elif val >= 10:
        return f'<span style="color:#ea580c;font-weight:600;">{val:.2f}%</span>'
    else:
        return f"{val:.2f}%"

# 定義欄位對應（✅ 融券餘額改為融資使用率）
col_map = {
    "stock_id":           "股票代號",
    "stock_name":         "公司名稱",
    "market":             "市場",
    "industry":           "產業別",
    "close_price":        "收盤價",
    "volume":             "成交量",
    "pe_ratio":           "P/E",
    "pb_ratio":           "P/B",
    "eps":                "EPS",
    "roe":                "ROE%",
    "book_value":         "每股淨值",
    "year_month":         "營收年月",
    "revenue":            "月營收(千)",
    "yoy_growth":         "年增率%",
    "mom_growth":         "月增率%",
    "foreign_net_shares": "外資買賣超",
    "sitca_net_shares":   "投信買賣超",
    "dealers_net_shares": "自營買賣超",
    "total_net_shares":   "法人合計",
    "big_holder_ratio":   "大戶持股%",
    "margin_balance":     "融資餘額",
    "margin_rate":        "融資使用率",  # ✅ 原本是融券餘額，改為融資使用率
}

available_cols = {k: v for k, v in col_map.items() if k in filtered.columns}
show_cols = list(available_cols.keys())
if "price_change" not in show_cols and "price_change" in filtered.columns:
    show_cols.append("price_change")

show_df = filtered[show_cols].copy()
show_df = show_df.sort_values("stock_id").reset_index(drop=True)

html_rows = []
for _, row in show_df.iterrows():
    html_rows.append({
        "股票代號":    row.get("stock_id", ""),
        "公司名稱":    row.get("stock_name", ""),
        "市場":        row.get("market", ""),
        "產業別":      row.get("industry", ""),
        "收盤價":      color_price(row.get("close_price"), row.get("price_change")),
        "成交量":      f"{row['volume']:,.0f}" if pd.notna(row.get("volume")) else "",
        "P/E":         fmt_pe(row.get("pe_ratio")),
        "P/B":         f"{row['pb_ratio']:,.2f}" if pd.notna(row.get("pb_ratio")) else "",
        "EPS":         color_value(row.get("eps")),
        "ROE%":        color_value(row.get("roe")),
        "每股淨值":    f"{row['book_value']:,.2f}" if pd.notna(row.get("book_value")) else "",
        "營收年月":    row.get("year_month", "") if pd.notna(row.get("year_month")) else "",
        "月營收(千)":  f"{row['revenue']:,.0f}" if pd.notna(row.get("revenue")) else "",
        "年增率%":     color_value(row.get("yoy_growth")),
        "月增率%":     color_value(row.get("mom_growth")),
        "外資買賣超":  color_int(row.get("foreign_net_shares")),
        "投信買賣超":  color_int(row.get("sitca_net_shares")),
        "自營買賣超":  color_int(row.get("dealers_net_shares")),
        "法人合計":    color_int(row.get("total_net_shares")),
        "大戶持股%":   f"{row['big_holder_ratio']:,.2f}" if pd.notna(row.get("big_holder_ratio")) else "",
        "融資餘額":    f"{row['margin_balance']:,.0f}" if pd.notna(row.get("margin_balance")) else "",
        "融資使用率":  color_rate(row.get("margin_rate")),  # ✅ 改為融資使用率
    })

html_df = pd.DataFrame(html_rows)
table_html = html_df.to_html(escape=False, index=False, classes="stock-table")

table_style = """
<style>
.stock-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    font-family: -apple-system, "Noto Sans TC", sans-serif;
}
.stock-table th {
    background: #f0f2f6;
    padding: 8px 6px;
    text-align: center;
    border-bottom: 2px solid #ddd;
    position: sticky;
    top: 0;
    white-space: nowrap;
    z-index: 1;
}
.stock-table td {
    padding: 6px 8px;
    text-align: right;
    border-bottom: 1px solid #eee;
    white-space: nowrap;
}
.stock-table td:nth-child(1), .stock-table td:nth-child(2),
.stock-table td:nth-child(3), .stock-table td:nth-child(4) {
    text-align: left;
}
.stock-table tr:hover { background: #f9f9f9; }
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
csv_df = filtered[show_cols].copy()
csv_df = csv_df.sort_values("stock_id").reset_index(drop=True)
csv = csv_df.to_csv(index=False, encoding="utf-8-sig")
st.download_button(
    label="📥 下載篩選結果 CSV",
    data=csv,
    file_name="台股篩選結果.csv",
    mime="text/csv",
)
