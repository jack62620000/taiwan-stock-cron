import streamlit as st
import pandas as pd
import numpy as np
import sys
import os
import altair as alt
from concurrent.futures import ThreadPoolExecutor

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.glossary import render_page_guide
from utils.ui import (
    require_login, style_sign, style_rate, render_metric_cards, UP_COLOR, DOWN_COLOR
)
from utils.db import (
    get_stock_list, get_latest_prices, get_latest_ins,
    get_latest_holder, get_latest_revenue,
    get_latest_quarterly, get_latest_margin_utilization, get_recent_closes,
    get_market_index, get_futures_ins, get_recent_ins
)
from utils.sector import render_sector_rotation
require_login()

st.set_page_config(
    page_title="全台股總覽",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed"
)
st.title("📊 全台股總覽")
render_page_guide("overview")

if st.button("🔄 強制重新整理資料"):
    st.cache_data.clear()
    get_stock_list.cache_clear()  # 股票清單（含產業別）也一併重新讀取
    st.rerun()

# ===== 載入資料 =====
def load_latest_rsi(closes, period=14):
    """
    用近期收盤價算每檔股票最新的 RSI(14)，算法與個股分析卡（indicators.calc_rsi）相同，
    但一次對全部股票做 groupby 計算，不逐檔迴圈。
    """
    if closes.empty:
        return pd.DataFrame(columns=["stock_id", "RSI"])
    closes = closes.copy()
    closes["close_price"] = pd.to_numeric(closes["close_price"], errors="coerce")
    closes = closes.sort_values(["stock_id", "date"]).reset_index(drop=True)
    # 停牌或沒有成交的日子收盤價是空值，視為股價不變（與 indicators.fill_no_trade 相同）
    closes["close_price"] = closes.groupby("stock_id", sort=False)["close_price"].ffill()
    grouped = closes.groupby("stock_id", sort=False)

    delta = grouped["close_price"].diff()
    closes["gain"] = delta.where(delta > 0, 0)
    closes["loss"] = (-delta).where(delta < 0, 0)
    avg = closes.groupby("stock_id", sort=False)[["gain", "loss"]] \
        .rolling(window=period).mean().reset_index(level=0)
    closes["avg_gain"] = avg["gain"]
    closes["avg_loss"] = avg["loss"]
    closes["RSI"] = (100 - 100 / (1 + closes["avg_gain"] / closes["avg_loss"])).round(2)

    out = closes.groupby("stock_id", sort=False).tail(1)[["stock_id", "RSI"]].copy()
    # 與 calc_rsi 一致：資料不足 period + 1 天不算
    counts = grouped.size()
    out.loc[out["stock_id"].map(counts) < period + 1, "RSI"] = np.nan
    return out.reset_index(drop=True)

@st.cache_data(ttl=3600)
def load_overview_data():
    # 「找最新日期」改查權值股後很快，這幾張表可以同時查，不必一張等一張
    loaders = [
        get_stock_list,
        get_latest_prices,
        get_latest_ins,
        get_latest_margin_utilization,
        get_latest_holder,
        get_latest_revenue,
        get_latest_quarterly,
        # 類股輪動要算成交值，所以多取 volume
        lambda: get_recent_closes(columns="stock_id, date, close_price, volume"),
    ]
    with st.spinner("正在載入資料..."), ThreadPoolExecutor(max_workers=4) as pool:
        data = list(pool.map(lambda f: f(), loaders))
    data.append(load_latest_rsi(data[-1]))
    return tuple(data)

stock_list, prices, ins, margin_util, holder, revenue, quarterly, closes, rsi = load_overview_data()
margin = margin_util  # 融資券餘額和使用率在同一張表

# ===== 大盤儀表板 =====
@st.cache_data(ttl=3600)
def load_market_data():
    """加權、櫃買指數近 120 個交易日，以及臺股期貨三大法人未平倉"""
    return get_market_index("TAIEX", 120), get_market_index("TPEX", 120), get_futures_ins()

@st.cache_data(ttl=3600)
def load_sector_ins():
    """類股輪動用：近 10 天外資、投信買賣超"""
    return get_recent_ins(calendar_days=10, columns="stock_id, date, foreign_net_shares, sitca_net_shares")

def market_breadth(closes):
    """每個交易日的上漲、下跌家數（和前一個交易日的還原收盤價比較，沒有成交的不算）"""
    if closes.empty:
        return pd.DataFrame(columns=["date", "上漲", "下跌"])
    c = closes.assign(close_price=pd.to_numeric(closes["close_price"], errors="coerce"))
    wide = c.pivot_table(index="date", columns="stock_id", values="close_price", aggfunc="last").sort_index()
    diff = wide.diff().iloc[1:]
    return pd.DataFrame({
        "date": diff.index.astype(str),
        "上漲": (diff > 0).sum(axis=1).values,
        "下跌": (diff < 0).sum(axis=1).values,
    })

def index_card(label, df):
    if df.empty:
        return (label, "—", "無資料", 0)
    last = df.iloc[-1]
    close, chg = float(last["close"]), float(last["change"])
    pct = chg / (close - chg) * 100 if close != chg else 0
    return (label, f"{close:,.2f}", f"{'▲' if chg > 0 else '▼' if chg < 0 else ''} {abs(chg):,.2f}（{pct:+.2f}%）", chg)

taiex, tpex, futures = load_market_data()
breadth = market_breadth(closes)

tab_market, tab_sector, tab_all = st.tabs(["📈 大盤", "🔄 類股輪動", "📋 全台股數據"])

with tab_market:
    cards = [index_card("加權指數", taiex), index_card("櫃買指數", tpex)]

    # 上市＋上櫃成交值（億元），和前一個交易日比較
    value = pd.concat([taiex, tpex])[["date", "trade_value"]] if not taiex.empty else pd.DataFrame()
    if not value.empty:
        value = value.groupby("date")["trade_value"].sum().sort_index() / 1e8
        v_now = value.iloc[-1]
        v_chg = (v_now / value.iloc[-2] - 1) * 100 if len(value) > 1 and value.iloc[-2] > 0 else 0
        cards.append(("上市櫃成交值", f"{v_now:,.0f} 億", f"較前一日 {v_chg:+.1f}%", v_chg))

    if not breadth.empty:
        up, down = int(breadth["上漲"].iloc[-1]), int(breadth["下跌"].iloc[-1])
        cards.append(("上漲／下跌家數", f"{up:,} / {down:,}",
                      f"上漲佔 {up / (up + down) * 100:.0f}%" if up + down else "", up - down))

    foreign_fut = futures[futures["item"] == "外資及陸資"].sort_values("date") if not futures.empty else futures
    if not foreign_fut.empty:
        oi = foreign_fut["net_oi"].astype(float)
        oi_chg = oi.iloc[-1] - oi.iloc[-2] if len(oi) > 1 else 0
        cards.append(("外資台指期淨未平倉", f"{oi.iloc[-1]:,.0f} 口",
                      f"較前一日 {oi_chg:+,.0f} 口", oi_chg))
    render_metric_cards(cards)

    chart_left, chart_right = st.columns(2)
    with chart_left:
        index_pick = st.radio("指數", ["加權指數", "櫃買指數"], horizontal=True, label_visibility="collapsed")
        idx = (taiex if index_pick == "加權指數" else tpex).copy()
        if idx.empty:
            st.info("無大盤指數資料")
        else:
            idx["date"] = idx["date"].astype(str)
            idx["收盤"] = pd.to_numeric(idx["close"], errors="coerce")
            idx["20 日均線"] = idx["收盤"].rolling(20).mean()
            idx_long = idx[["date", "收盤", "20 日均線"]].melt("date", var_name="線", value_name="點數").dropna()
            step = max(1, len(idx) // 6)
            st.altair_chart(alt.Chart(idx_long).mark_line(strokeWidth=2).encode(
                x=alt.X("date:O", title=None, axis=alt.Axis(values=idx["date"].iloc[::step].tolist(), labelAngle=0)),
                y=alt.Y("點數:Q", title=f"{index_pick}（近 {len(idx)} 個交易日）", scale=alt.Scale(zero=False)),
                color=alt.Color("線:N", scale=alt.Scale(domain=["收盤", "20 日均線"], range=["#fbbf24", "#9ca3af"]),
                                legend=alt.Legend(orient="top", title=None)),
                tooltip=[alt.Tooltip("date:N", title="日期"), alt.Tooltip("線:N"),
                         alt.Tooltip("點數:Q", format=",.2f")],
            ).properties(height=260), width="stretch")

    with chart_right:
        st.markdown("**外資台指期淨未平倉（近 60 個交易日）**")
        if foreign_fut.empty:
            st.info("無期貨資料")
        else:
            fut = foreign_fut.tail(60)[["date", "net_oi"]].copy()
            fut["date"] = fut["date"].astype(str)
            fut["net_oi"] = fut["net_oi"].astype(float)
            step = max(1, len(fut) // 6)
            st.altair_chart(alt.Chart(fut).mark_bar().encode(
                x=alt.X("date:O", title=None, axis=alt.Axis(values=fut["date"].iloc[::step].tolist(), labelAngle=0)),
                y=alt.Y("net_oi:Q", title="淨未平倉（口）", axis=alt.Axis(format="~s")),
                color=alt.condition(alt.datum.net_oi >= 0, alt.value(UP_COLOR), alt.value(DOWN_COLOR)),
                tooltip=[alt.Tooltip("date:N", title="日期"), alt.Tooltip("net_oi:Q", title="淨未平倉（口）", format=",")],
            ).properties(height=260), width="stretch")

    if not breadth.empty:
        st.markdown(f"**每日漲跌家數（近 {len(breadth)} 個交易日）**")
        b_long = breadth.assign(下跌=-breadth["下跌"]).melt("date", var_name="漲跌", value_name="家數")
        st.altair_chart(alt.Chart(b_long).mark_bar().encode(
            x=alt.X("date:O", title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("家數:Q", title="家數（下跌畫在零軸下方）"),
            color=alt.Color("漲跌:N", scale=alt.Scale(domain=["上漲", "下跌"], range=[UP_COLOR, DOWN_COLOR]),
                            legend=alt.Legend(orient="top", title=None)),
            tooltip=[alt.Tooltip("date:N", title="日期"), alt.Tooltip("漲跌:N"),
                     alt.Tooltip("家數:Q", title="家數")],
        ).properties(height=200), width="stretch")
        st.caption("漲跌家數用還原權值後的收盤價和前一個交易日比較；上漲家數持續多於下跌家數，代表大部分股票一起走強，不是只有權值股撐盤。")

with tab_sector:
    try:
        sector_ins = load_sector_ins()
    except Exception:
        sector_ins = pd.DataFrame()
        st.warning("法人資料暫時讀取失敗，產業的外資、投信金額先不顯示。")
    render_sector_rotation(closes, sector_ins, stock_list)


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

# 合併 RSI
if not rsi.empty:
    df = df.merge(rsi, on="stock_id", how="left")

# 合併三大法人
if not ins.empty:
    latest_ins_date = ins["date"].max()
    ins_latest = ins[ins["date"] == latest_ins_date].copy()
    ins_latest = ins_latest.drop_duplicates(subset="stock_id", keep="first")
    # 法人合計＝外資＋投信＋自營商（資料庫不另存合計，節省空間）
    ins_cols = ["foreign_net_shares", "sitca_net_shares", "dealers_net_shares"]
    ins_latest["total_net_shares"] = ins_latest[ins_cols].sum(axis=1, min_count=1)
    df = df.merge(
        ins_latest[["stock_id", *ins_cols, "total_net_shares"]],
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

# 用最新收盤價重算 P/B（資料庫存的是抓季報當下的股價，放久會過時）
if "book_value" in df.columns and "close_price" in df.columns:
    pb_valid = df["book_value"].notna() & (df["book_value"] > 0) & df["close_price"].notna()
    df["pb_ratio"] = (df["close_price"] / df["book_value"]).where(pb_valid).round(2)

# 計算本益比
# 季報的 EPS 是「年初至該季」的累計值，年化要用 EPS × 4 ÷ 季別
# （第 1 季 ×4、第 2 季累計 ×2、第 3 季累計 ×4/3、第 4 季累計 ×1）
if "eps" in df.columns and "close_price" in df.columns:
    def calc_pe(row):
        eps = row.get("eps")
        close = row.get("close_price")
        quarter = row.get("quarter")
        if pd.isna(eps):
            return None
        if eps <= 0:
            return "虧損"
        if pd.isna(close) or pd.isna(quarter) or quarter <= 0:
            return None
        return round(close / (eps * 4 / quarter), 2)
    df["pe_ratio"] = df.apply(calc_pe, axis=1)

# 計算漲跌
if "open_price" in df.columns and "close_price" in df.columns:
    df["price_change"] = df["close_price"] - df["open_price"]

# ===== 側邊篩選器 =====
st.sidebar.header("🔍 篩選條件")
st.sidebar.caption("只套用在「📋 全台股數據」分頁。")
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

if "RSI" in filtered.columns:
    filtered = filtered[filtered["RSI"].isna() | ((filtered["RSI"] >= rsi_min) & (filtered["RSI"] <= rsi_max))]

with tab_all:
    # ===== 顯示統計 =====
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("篩選後股票數", f"{len(filtered):,} 檔")
    col2.metric("上市", f"{len(filtered[filtered['market']=='上市']):,} 檔")
    col3.metric("上櫃", f"{len(filtered[filtered['market']=='上櫃']):,} 檔")
    col4.metric("資料日期", prices["date"].max() if not prices.empty else "N/A")

    st.markdown("---")

    # ===== 顯示資料表 =====
    st.caption("點欄位名稱可排序，滑到表格右上角可搜尋、全螢幕檢視。")

    DOWN_STYLE = f"color:{DOWN_COLOR};font-weight:600;"  # 虧損標示用

    # 定義欄位對應（✅ 融券餘額改為融資使用率）
    col_map = {
        "stock_id":           "股票代號",
        "stock_name":         "公司名稱",
        "market":             "市場",
        "industry":           "產業別",
        "close_price":        "收盤價",
        "volume":             "成交量",
        "pe_ratio":           "P/E(年化)",
        "pb_ratio":           "P/B",
        "eps":                "EPS(累計)",
        "roe":                "ROE%(累計)",
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
        "margin_balance":     "融資餘額(張)",
        "margin_rate":        "融資使用率",  # ✅ 原本是融券餘額，改為融資使用率
        "RSI":                "RSI",
    }

    available_cols = {k: v for k, v in col_map.items() if k in filtered.columns}
    show_cols = list(available_cols.keys())
    if "price_change" in filtered.columns:
        show_cols.append("price_change")  # 收盤價上色用，不顯示

    show_df = filtered[show_cols].copy()
    show_df = show_df.sort_values("stock_id").reset_index(drop=True)

    # P/E 轉成數字才能排序；虧損的公司設為無限大（排序時排在最貴的一端），顯示時標「虧損」
    loss_mask = pd.Series(False, index=show_df.index)
    if "pe_ratio" in show_df.columns:
        loss_mask = show_df["pe_ratio"] == "虧損"
        show_df["pe_ratio"] = pd.to_numeric(show_df["pe_ratio"].where(~loss_mask), errors="coerce")
        show_df.loc[loss_mask, "pe_ratio"] = np.inf

    price_change = show_df.pop("price_change") if "price_change" in show_df.columns \
        else pd.Series(index=show_df.index, dtype=float)

    display_df = show_df.rename(columns=available_cols)
    present = set(display_df.columns)
    def cols(*names):
        return [c for c in names if c in present]

    fmt_2   = cols("收盤價", "P/B", "EPS(累計)", "ROE%(累計)", "每股淨值",
                   "年增率%", "月增率%", "大戶持股%", "RSI", "P/E(年化)")
    fmt_int = cols("成交量", "月營收(千)", "外資買賣超", "投信買賣超",
                   "自營買賣超", "法人合計", "融資餘額(張)")
    sign_cols = cols("EPS(累計)", "ROE%(累計)", "年增率%", "月增率%",
                     "外資買賣超", "投信買賣超", "自營買賣超", "法人合計")

    styler = display_df.style \
        .format("{:,.2f}", subset=fmt_2, na_rep="") \
        .format("{:,.0f}", subset=fmt_int, na_rep="") \
        .map(style_sign, subset=sign_cols)

    if "融資使用率" in present:
        styler = styler.format("{:.2f}%", subset=["融資使用率"], na_rep="") \
            .map(style_rate, subset=["融資使用率"])

    if "收盤價" in present:
        # 收盤價依當日漲跌上色
        styler = styler.apply(
            lambda s: [style_sign(c) for c in price_change], subset=["收盤價"], axis=0
        )

    if "P/E(年化)" in present and loss_mask.any():
        loss_idx = show_df.index[loss_mask]
        styler = styler.format(lambda _: "虧損", subset=pd.IndexSlice[loss_idx, ["P/E(年化)"]]) \
            .map(lambda _: DOWN_STYLE, subset=pd.IndexSlice[loss_idx, ["P/E(年化)"]])

    st.dataframe(
        styler,
        hide_index=True,
        width="stretch",
        height=700,
        column_config={
            "股票代號": st.column_config.TextColumn(pinned=True),
            "公司名稱": st.column_config.TextColumn(pinned=True),
        },
    )

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
