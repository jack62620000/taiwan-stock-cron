import streamlit as st
import pandas as pd
import altair as alt
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.db import get_stock_list, get_recent_closes, get_recent_ins
from utils.ui import require_login, style_sign, UP_COLOR, DOWN_COLOR
require_login()

st.set_page_config(page_title="類股輪動", page_icon="🔄", layout="wide")
st.title("🔄 類股輪動")
st.caption("依產業別比較近 5 日、20 日漲幅與外資、投信買賣超金額，看資金正在流向哪些產業。")

if st.button("🔄 強制重新整理資料"):
    st.cache_data.clear()
    get_stock_list.cache_clear()
    st.rerun()


# ===== 載入資料 =====
@st.cache_data(ttl=3600)
def load_sector_data():
    with st.spinner("正在載入近 40 天全市場股價與法人資料，第一次需要一點時間..."):
        closes = get_recent_closes(calendar_days=40, columns="stock_id, date, close_price, volume")
        ins = get_recent_ins(calendar_days=10, columns="stock_id, date, foreign_net_shares, sitca_net_shares")
    return closes, ins


def stock_stats(closes, ins):
    """每檔股票的 5、20 日漲幅（還原權值）、20 日均成交值、外資與投信 5 日買賣超金額"""
    c = closes.copy()
    c["close_price"] = pd.to_numeric(c["close_price"], errors="coerce")
    c["volume"] = pd.to_numeric(c["volume"], errors="coerce").fillna(0)
    close = c.pivot_table(index="date", columns="stock_id", values="close_price", aggfunc="last").sort_index().ffill(limit=5)
    value = (c["close_price"] * c["volume"]).groupby([c["date"], c["stock_id"]]).sum().unstack().sort_index()
    out = pd.DataFrame({
        "收盤價": close.iloc[-1],
        "5日%": (close.iloc[-1] / close.iloc[-6] - 1) * 100 if len(close) > 5 else None,
        "20日%": (close.iloc[-1] / close.iloc[-21] - 1) * 100 if len(close) > 20 else None,
        "均成交值": value.tail(20).mean(),
    })
    if not ins.empty:
        i = ins.copy()
        last5 = sorted(i["date"].unique())[-5:]
        i = i[i["date"].isin(last5)]
        price = close.iloc[-1]
        for col, name in (("foreign_net_shares", "外資5日(億)"), ("sitca_net_shares", "投信5日(億)")):
            shares = pd.to_numeric(i[col], errors="coerce").groupby(i["stock_id"]).sum()
            out[name] = shares * price.reindex(shares.index) / 1e8
    return out.rename_axis("stock_id").reset_index(), close


closes, ins = load_sector_data()
stock_list = get_stock_list()
if closes.empty or stock_list.empty:
    st.error("無股價資料")
    st.stop()

stats, close_wide = stock_stats(closes, ins)
stats = stats.merge(stock_list[["stock_id", "stock_name", "industry"]], on="stock_id", how="inner")
stats = stats[stats["industry"].notna() & stats["20日%"].notna()]

weighting = st.radio("產業漲幅計算方式", ["成交值加權", "等權平均"], horizontal=True,
                     help="成交值加權：成交越熱絡的股票影響越大，較能反映資金實際流向；等權平均：每檔股票一樣重要，小型股影響較大。")


def sector_table(df):
    rows = []
    for ind, g in df.groupby("industry"):
        w = g["均成交值"].clip(lower=0) if weighting == "成交值加權" else pd.Series(1.0, index=g.index)
        if w.sum() <= 0:
            w = pd.Series(1.0, index=g.index)
        rows.append({
            "產業": ind,
            "檔數": len(g),
            "5日%": (g["5日%"] * w).sum() / w[g["5日%"].notna()].sum() if g["5日%"].notna().any() else None,
            "20日%": (g["20日%"] * w).sum() / w.sum(),
            "上漲比例%": (g["20日%"] > 0).mean() * 100,
            "外資5日(億)": g["外資5日(億)"].sum() if "外資5日(億)" in g else None,
            "投信5日(億)": g["投信5日(億)"].sum() if "投信5日(億)" in g else None,
        })
    return pd.DataFrame(rows).sort_values("20日%", ascending=False).reset_index(drop=True)


sectors = sector_table(stats)

# ===== 產業 20 日漲幅排行 =====
st.subheader(f"📊 產業近 20 日漲幅排行（{close_wide.index[-1]}）")
bar = alt.Chart(sectors).mark_bar().encode(
    y=alt.Y("產業:N", sort="-x", title=None),
    x=alt.X("20日%:Q", title="近 20 日漲幅（%）"),
    color=alt.condition(alt.datum["20日%"] >= 0, alt.value(UP_COLOR), alt.value(DOWN_COLOR)),
    tooltip=[alt.Tooltip("產業:N"), alt.Tooltip("20日%:Q", format="+.2f"), alt.Tooltip("5日%:Q", format="+.2f"),
             alt.Tooltip("外資5日(億):Q", format="+,.1f"), alt.Tooltip("投信5日(億):Q", format="+,.1f")],
).properties(height=max(300, 18 * len(sectors)))
st.altair_chart(bar, width="stretch")

# ===== 資金流向象限 =====
st.subheader("🧭 輪動象限：短線動能 vs 中期趨勢")
quad = alt.Chart(sectors).mark_circle(opacity=0.8).encode(
    x=alt.X("20日%:Q", title="近 20 日漲幅（%）"),
    y=alt.Y("5日%:Q", title="近 5 日漲幅（%）"),
    size=alt.Size("檔數:Q", legend=None, scale=alt.Scale(range=[60, 600])),
    color=alt.condition(alt.datum["外資5日(億)"] + alt.datum["投信5日(億)"] >= 0, alt.value(UP_COLOR), alt.value(DOWN_COLOR)),
    tooltip=[alt.Tooltip("產業:N"), alt.Tooltip("20日%:Q", format="+.2f"), alt.Tooltip("5日%:Q", format="+.2f"),
             alt.Tooltip("外資5日(億):Q", format="+,.1f"), alt.Tooltip("投信5日(億):Q", format="+,.1f")],
)
labels = quad.mark_text(dy=-12, fontSize=11, color="#a3a3a3").encode(text="產業:N", size=alt.value(11))
rules = alt.Chart(pd.DataFrame({"v": [0]})).mark_rule(color="#9ca3af", strokeDash=[4, 4])
st.altair_chart((rules.encode(x="v:Q") + rules.encode(y="v:Q") + quad + labels).properties(height=440), width="stretch")
st.caption("右上：中期強、短線也還在漲（強勢延續）；左上：中期弱但最近 5 日轉強（可能開始輪動進來）；"
           "右下：中期強但最近轉弱（可能開始輪動出去）；左下：持續弱勢。圓點紅色代表外資＋投信 5 日合計買超，綠色是賣超；圓越大檔數越多。")

# ===== 產業總表 =====
st.subheader("📋 產業總表")
sign_cols = ["5日%", "20日%", "外資5日(億)", "投信5日(億)"]
st.dataframe(
    sectors.style.format("{:+.2f}", subset=["5日%", "20日%"], na_rep="")
        .format("{:.0f}", subset=["上漲比例%"])
        .format("{:+,.1f}", subset=["外資5日(億)", "投信5日(億)"], na_rep="")
        .map(style_sign, subset=sign_cols),
    hide_index=True, width="stretch",
)
st.caption("漲幅用還原權值後的收盤價；上漲比例是近 20 日上漲的股票占該產業的比例；"
           "外資、投信買賣超金額＝5 日買賣超股數 × 最新收盤價，單位億元。")

# ===== 產業內個股 =====
st.subheader("🔍 產業內個股")
pick = st.selectbox("選擇產業", sectors["產業"].tolist())
members = stats[stats["industry"] == pick].sort_values("20日%", ascending=False)
members = members.assign(均成交值=members["均成交值"] / 1e8).rename(
    columns={"stock_id": "代號", "stock_name": "名稱", "均成交值": "20日均成交值(億)"})
member_cols = ["代號", "名稱", "收盤價", "5日%", "20日%", "20日均成交值(億)", "外資5日(億)", "投信5日(億)"]
members = members[[c for c in member_cols if c in members.columns]]
st.dataframe(
    members.style.format("{:,.2f}", subset=["收盤價", "20日均成交值(億)"], na_rep="")
        .format("{:+.2f}", subset=["5日%", "20日%"], na_rep="")
        .format("{:+,.2f}", subset=[c for c in ["外資5日(億)", "投信5日(億)"] if c in members.columns], na_rep="")
        .map(style_sign, subset=[c for c in sign_cols if c in members.columns]),
    hide_index=True, width="stretch",
    column_config={"代號": st.column_config.TextColumn(pinned=True), "名稱": st.column_config.TextColumn(pinned=True)},
)
