import streamlit as st
import pandas as pd
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.db import get_stock_list, get_recent_closes
from utils.granville import RULES, granville_signals
from utils.ui import require_login, style_sign, UP_COLOR, DOWN_COLOR
require_login()

st.set_page_config(
    page_title="葛蘭碧八大法則",
    page_icon="📐",
    layout="wide",
    initial_sidebar_state="expanded"
)
st.title("📐 葛蘭碧八大法則選股")

if st.button("🔄 強制重新整理資料"):
    st.cache_data.clear()
    get_stock_list.cache_clear()
    st.rerun()

# ===== 載入資料 =====
@st.cache_data(ttl=3600)
def load_prices():
    # 60 個日曆天約 40 個交易日，夠算 20 日均線與它前一週的方向
    with st.spinner("正在載入近 60 天股價..."):
        return get_recent_closes(calendar_days=60, columns="stock_id, date, close_price, volume")

stock_list = get_stock_list()
prices = load_prices()

if prices.empty:
    st.warning("查無股價資料")
    st.stop()

latest_date = prices["date"].max()

# ===== 側邊參數 =====
st.sidebar.header("⚙️ 判斷參數")
ma_days = st.sidebar.selectbox("均線", [20, 10], format_func=lambda d: f"{d} 日均線")
slope_days = st.sidebar.number_input("均線方向：與幾天前的均線比較", 2, 10, 5)
lookback = st.sidebar.number_input("法則 2、3、6、7：往回看幾天", 2, 10, 5)
near_pct = st.sidebar.number_input("法則 3、7：靠近均線的距離（%）", 0.5, 10.0, 2.0, step=0.5)
bias_pct = st.sidebar.number_input("法則 4、8：乖離過大門檻（%）", 3.0, 50.0, 10.0, step=1.0)

st.sidebar.header("🔍 篩選條件")
search_id = st.sidebar.text_input("股票代號（可輸入多個，用逗號分隔）", placeholder="例如: 2330,2454")
markets = ["全部"] + sorted(stock_list["market"].dropna().unique().tolist())
selected_market = st.sidebar.selectbox("市場", markets)
industries = ["全部"] + sorted(stock_list["industry"].dropna().unique().tolist())
selected_industry = st.sidebar.selectbox("產業別", industries)
min_volume = st.sidebar.number_input("當日成交量下限（張）", 0, 1000000, 0, step=100)

# ===== 計算訊號 =====
signals = granville_signals(
    prices, ma_days=ma_days, slope_days=slope_days, lookback=lookback,
    near_pct=near_pct, bias_pct=bias_pct,
)

volume = prices[prices["date"] == latest_date][["stock_id", "volume"]] \
    .drop_duplicates(subset="stock_id")
volume["volume"] = pd.to_numeric(volume["volume"], errors="coerce") / 1000  # 股 → 張

df = signals.merge(stock_list[["stock_id", "stock_name", "market", "industry"]], on="stock_id", how="inner") \
    .merge(volume, on="stock_id", how="left")

if search_id:
    ids = [s.strip() for s in search_id.split(",") if s.strip()]
    df = df[df["stock_id"].isin(ids)]
if selected_market != "全部":
    df = df[df["market"] == selected_market]
if selected_industry != "全部":
    df = df[df["industry"] == selected_industry]
if min_volume > 0:
    df = df[df["volume"] >= min_volume]

# ===== 各法則檔數 =====
counts = {n: int(df["rules"].apply(lambda r: n in r).sum()) for n in RULES}

st.caption(f"資料日期：{latest_date}　·　依 {ma_days} 日均線判斷，共 {len(df):,} 檔有足夠資料")

buy_cols = st.columns(4)
sell_cols = st.columns(4)
for n, (side, desc) in RULES.items():
    col = buy_cols[n - 1] if n <= 4 else sell_cols[n - 5]
    col.metric(f"法則{n}（{side}）", f"{counts[n]:,} 檔", help=desc)

with st.expander("📖 八大法則說明與判斷方式"):
    st.markdown("\n".join(f"- **法則{n}（{side}）**：{desc}" for n, (side, desc) in RULES.items()))
    st.markdown(f"""
**程式怎麼判斷（以最新一個交易日為準，參數可在左側調整）**
- 均線上揚／下彎：今天的均線比 {slope_days} 天前高／低。
- 法則 1、5：今天剛突破／跌破均線，而且 {slope_days} 天前的均線方向是相反或走平的（代表均線正在轉折）。
- 法則 2、6：均線方向一直沒變，過去 {lookback} 天內曾經跌破／突破均線，今天又回到原本那一側。
- 法則 3、7：過去 {lookback} 天都在均線同一側，最接近均線時乖離在 {near_pct}% 以內，今天往遠離均線的方向走。
- 法則 4、8：乖離率（收盤價與均線的差距）超過 ±{bias_pct}%。
""")
    st.caption("技術分析僅供參考，請搭配其他指標與基本面判斷。")

st.markdown("---")

# ===== 結果表 =====
rule_options = list(RULES)
selected_rules = st.multiselect(
    "顯示符合這些法則的股票",
    rule_options,
    default=rule_options,
    format_func=lambda n: f"法則{n}（{RULES[n][0]}）",
)

result = df[df["rules"].apply(lambda r: any(n in selected_rules for n in r))].copy()

def signal_of(rules):
    sides = {RULES[n][0] for n in rules}
    return "、".join(s for s in ["買進", "賣出"] if s in sides)

result["signal"] = result["rules"].apply(signal_of)
result["rule_text"] = result["rules"].apply(lambda r: "、".join(f"法則{n}" for n in r))
result["first_rule"] = result["rules"].str[0]  # 依法則編號、股票代號排序
result = result.sort_values(["first_rule", "stock_id"]).reset_index(drop=True)

st.subheader(f"📋 符合條件的股票（{len(result):,} 檔）")

if result.empty:
    st.info("目前沒有股票符合選取的法則")
    st.stop()

col_map = {
    "stock_id":    "股票代號",
    "stock_name":  "公司名稱",
    "market":      "市場",
    "industry":    "產業別",
    "signal":      "訊號",
    "rule_text":   "符合法則",
    "close_price": "收盤價",
    "change":      "漲跌",
    "ma":          f"{ma_days}日均線",
    "bias":        "乖離率%",
    "ma_trend":    "均線方向",
    "volume":      "成交量(張)",
}
display_df = result[list(col_map)].rename(columns=col_map)

def style_signal(val):
    if val == "買進":
        return f"color:{UP_COLOR};font-weight:700;"
    if val == "賣出":
        return f"color:{DOWN_COLOR};font-weight:700;"
    return ""

def style_trend(val):
    return style_sign({"上揚": 1, "下彎": -1}.get(val, 0))

change = result["change"]
styler = display_df.style \
    .format("{:,.2f}", subset=["收盤價", "漲跌", f"{ma_days}日均線", "乖離率%"], na_rep="") \
    .format("{:,.0f}", subset=["成交量(張)"], na_rep="") \
    .map(style_sign, subset=["漲跌", "乖離率%"]) \
    .map(style_signal, subset=["訊號"]) \
    .map(style_trend, subset=["均線方向"]) \
    .apply(lambda s: [style_sign(c) for c in change], subset=["收盤價"], axis=0)

st.dataframe(
    styler,
    hide_index=True,
    width="stretch",
    height=600,
    column_config={
        "股票代號": st.column_config.TextColumn(pinned=True),
        "公司名稱": st.column_config.TextColumn(pinned=True),
    },
)

# ===== 下載按鈕 =====
csv = display_df.to_csv(index=False, encoding="utf-8-sig")
st.download_button(
    label="📥 下載篩選結果 CSV",
    data=csv,
    file_name=f"葛蘭碧八大法則_{latest_date}.csv",
    mime="text/csv",
)
