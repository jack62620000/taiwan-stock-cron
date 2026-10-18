import streamlit as st
import pandas as pd
import altair as alt
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.db import (
    get_stock_list, get_signal_dates, get_signals,
    get_signal_history, get_signal_performance,
)
from utils.ui import require_login, UP_COLOR, DOWN_COLOR
require_login()

st.set_page_config(page_title="每日選股訊號", page_icon="🎯", layout="wide")
st.title("🎯 每日選股訊號")
st.caption(
    "每個交易日收盤後，依固定規則幫全市場股票打分數："
    "總分 100 = 籌碼 32 + 資金 8 + 基本面 35 + 技術面 25（規則 v3）。"
    "排名只列 20 日均量 500 張以上的股票。風險提醒不扣分，僅供參考。"
)

if st.button("🔄 強制重新整理資料"):
    st.cache_data.clear()
    get_stock_list.cache_clear()
    st.rerun()

DIMENSIONS = {
    "chip_score": "籌碼",
    "margin_score": "資金",
    "fund_score": "基本面",
    "tech_score": "技術",
}
# 各面向滿分（規則 v3）
MAX_SCORE = {"chip_score": 32, "margin_score": 8, "fund_score": 35, "tech_score": 25}


# ===== 載入資料 =====
@st.cache_data(ttl=3600)
def load_dates():
    return get_signal_dates(n=250)

@st.cache_data(ttl=3600)
def load_day(date):
    df = get_signals(date)
    if df.empty:
        return df
    info = get_stock_list(include_delisted=True)[["stock_id", "stock_name", "industry", "market"]]
    return df.merge(info, on="stock_id", how="left")

@st.cache_data(ttl=3600)
def load_history(stock_id):
    return get_signal_history(stock_id, limit=120)

@st.cache_data(ttl=3600)
def load_performance(top_n, horizon):
    return get_signal_performance(top_n, horizon)


dates = load_dates()
if not dates:
    st.warning("還沒有評分資料。請到 GitHub Actions 執行「訊號評分回補」，或等今晚的每日排程跑完。")
    st.stop()

tab_rank, tab_stock, tab_backtest = st.tabs(["📋 今日排行榜", "📈 個股分數走勢", "🧪 規則回測"])

# =====================================================================
# 排行榜
# =====================================================================
with tab_rank:
    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        date = st.selectbox("評分日期", dates, index=0)
    with col2:
        top_n = st.number_input("顯示前幾名", min_value=10, max_value=300, value=30, step=10)
    with col3:
        min_total = st.slider("最低總分", 0, 100, 0, step=5)

    day = load_day(date)
    if day.empty:
        st.warning(f"{date} 沒有評分資料")
        st.stop()
    # 和 signals.py 的 KEEP_FULL_DAYS 一致：更早的評分日已精簡
    if dates.index(date) >= 20:
        st.caption("📦 這一天超過 20 個評分日，為了節省資料庫空間，只保留回測需要的股票，也不含加分理由；分數與排名不變。")

    with st.expander("🔍 進階篩選", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        mins = {}
        for c, (key, label) in zip([c1, c2, c3, c4], DIMENSIONS.items()):
            with c:
                mins[key] = st.slider(f"{label}至少", 0, MAX_SCORE[key], 0, key=f"min_{key}")
        c5, c6, c7 = st.columns([1, 2, 1])
        with c5:
            markets = sorted(day["market"].dropna().unique().tolist())
            pick_markets = st.multiselect("市場", markets, default=markets)
        with c6:
            industries = sorted(day["industry"].dropna().unique().tolist())
            pick_industries = st.multiselect("產業（不選＝全部）", industries)
        with c7:
            include_illiquid = st.checkbox("包含成交量小的股票", value=False)
            hide_warnings = st.checkbox("排除有風險提醒的", value=False)

    view = day.copy()
    if not include_illiquid:
        view = view[view["liquid"]]
    view = view[view["total_score"] >= min_total]
    for key, v in mins.items():
        view = view[view[key] >= v]
    if pick_markets:
        view = view[view["market"].isin(pick_markets)]
    if pick_industries:
        view = view[view["industry"].isin(pick_industries)]
    if hide_warnings:
        view = view[view["warnings"].apply(lambda w: not w)]
    view = view.sort_values(
        ["total_score", "chip_score", "avg_volume_20"], ascending=False
    ).head(int(top_n))

    # 摘要
    liquid = day[day["liquid"]]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("評分股票數", f"{len(day):,}")
    m2.metric("列入排名", f"{len(liquid):,}")
    m3.metric("全市場平均分", f"{liquid['total_score'].mean():.1f}")
    m4.metric("80 分以上", f"{(liquid['total_score'] >= 80).sum():,} 檔")

    st.markdown(f"#### {date} 排行榜（符合條件 {len(view)} 檔）")
    table = view[[
        "rank", "stock_id", "stock_name", "industry", "total_score",
        "chip_score", "margin_score", "fund_score", "tech_score",
        "close_price", "change_5d", "change_20d", "avg_volume_20", "reasons", "warnings",
    ]]
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        height=min(42 + 35 * len(table), 760),
        column_config={
            "rank": st.column_config.NumberColumn("排名", width="small"),
            "stock_id": st.column_config.TextColumn("代號", width="small"),
            "stock_name": st.column_config.TextColumn("名稱", width="small"),
            "industry": st.column_config.TextColumn("產業", width="small"),
            "total_score": st.column_config.ProgressColumn("總分", min_value=0, max_value=100, format="%d"),
            "chip_score": st.column_config.ProgressColumn("籌碼", min_value=0, max_value=32, format="%d"),
            "margin_score": st.column_config.ProgressColumn("資金", min_value=0, max_value=8, format="%d"),
            "fund_score": st.column_config.ProgressColumn("基本面", min_value=0, max_value=35, format="%d"),
            "tech_score": st.column_config.ProgressColumn("技術", min_value=0, max_value=25, format="%d"),
            "close_price": st.column_config.NumberColumn("收盤", format="%.2f"),
            "change_5d": st.column_config.NumberColumn("5日%", format="%.2f"),
            "change_20d": st.column_config.NumberColumn("20日%", format="%.2f"),
            "avg_volume_20": st.column_config.NumberColumn("20日均量(張)", format="%d"),
            "reasons": st.column_config.ListColumn("加分理由", width="large"),
            "warnings": st.column_config.ListColumn("風險提醒", width="medium"),
        },
    )

    with st.expander("📖 評分規則說明"):
        st.markdown("""
| 面向 | 項目 | 分數 |
|---|---|---|
| **籌碼（32）** | 外資 5 日買超 / 買超佔成交量 ≥ 5% / 連買 ≥ 3 天 | 4 / 3 / 3 |
| | 投信 5 日買超 / 連買 ≥ 3 天 | 7 / 8 |
| | 大戶持股週增（≥ 0.5 個百分點給滿分） | 4～7 |
| **資金（8）** | 券資比 ≥ 10% / ≥ 5% | 8 / 4 |
| **基本面（35）** | 月營收年增 > 0 / ≥ 20% | 4 / 8 |
| | 累計營收年增 > 0 | 4 |
| | 營收連 3 月年增 | 10 |
| | 營收創近期新高 | 13 |
| **技術（25）** | 站上月線 | 10 |
| | RSI 在 50～75 | 4 |
| | 20 日漲幅為正 | 11 |

**規則 v3 調整了什麼**（2026-10-17，法人、融資、月營收補到 2025 年後，用 2025-02～2026-09 約 400 個交易日驗證）：
- 拿掉「融資使用率低」「資減價漲」：沒有效果，最近一年甚至讓之後的報酬變差。
- 空出的 17 分加到前後兩段期間都有效的因子：投信 5 日買超、投信連買、站上月線、近 20 日上漲。
- 每天總分前 20 名之後 20 個交易日比全體平均多賺：v2 4.26%，v3 4.90%。

**規則 v2 調整了什麼**（2026-10-14，用 6～8 月資料設計、9～10 月資料驗證）：
- 拿掉「站上季線」「均線多頭排列」「量增價漲」：這三項在兩段期間都讓之後的報酬變差。
- 「營收連 3 月年增」「營收創新高」加重：兩段期間都有正向效果，基本面改為 35 分。
- 「近 20 日已大漲」不扣分：資料顯示大漲股之後並沒有比較差，只保留風險提醒。

**風險提醒**（不扣分）：RSI ≥ 80、近 20 日漲幅 ≥ 30%、融資使用率 ≥ 30%、融資 5 日暴增 ≥ 20%、外資投信同步賣超。

**資料時間**：只使用評分日當天收盤後已公開的資料。月營收在次月 11 日後才計入，大戶持股使用評分日前最新一週。
""")

# =====================================================================
# 個股分數走勢 + AI 解讀
# =====================================================================
with tab_stock:
    # 排行榜上的股票排在最前面，其餘依分數
    ordered = pd.concat([view, day.sort_values("total_score", ascending=False)]).drop_duplicates("stock_id")
    options = (ordered["stock_id"] + " " + ordered["stock_name"].fillna("")).tolist()
    selected = st.selectbox("選擇股票（排行榜上的股票排在前面）", options, index=0)
    stock_id = selected.split(" ", 1)[0]
    row = day[day["stock_id"] == stock_id].iloc[0]

    c1, c2, c3, c4, c5 = st.columns(5)
    rank_text = f"第 {int(row['rank'])} 名" if pd.notna(row["rank"]) else "未列入排名"
    c1.metric("總分", int(row["total_score"]), rank_text, delta_color="off")
    for c, (key, label) in zip([c2, c3, c4, c5], DIMENSIONS.items()):
        c.metric(label, f"{int(row[key])} / {MAX_SCORE[key]}")

    r1, r2 = st.columns(2)
    with r1:
        st.markdown("**✅ 加分理由**")
        st.markdown("\n".join(f"- {r}" for r in row["reasons"]) if row["reasons"] else "（無）")
    with r2:
        st.markdown("**⚠️ 風險提醒**")
        st.markdown("\n".join(f"- {w}" for w in row["warnings"]) if row["warnings"] else "（無）")

    hist = load_history(stock_id)
    if not hist.empty:
        long = hist.melt(
            id_vars="date",
            value_vars=["total_score"] + list(DIMENSIONS),
            var_name="面向", value_name="分數",
        )
        long["面向"] = long["面向"].map({"total_score": "總分", **DIMENSIONS})
        long["date"] = pd.to_datetime(long["date"])
        chart = alt.Chart(long).mark_line(point=alt.OverlayMarkDef(size=20)).encode(
            x=alt.X("date:T", title=None, axis=alt.Axis(format="%m/%d")),
            y=alt.Y("分數:Q", scale=alt.Scale(domain=[0, 100])),
            color=alt.Color("面向:N", sort=["總分", "籌碼", "資金", "基本面", "技術"]),
            tooltip=["date:T", "面向:N", "分數:Q"],
        ).properties(height=320)
        st.altair_chart(chart, width="stretch")

    b1, b2 = st.columns(2)
    with b1:
        if st.button("🗂️ 前往個股分析卡", width="stretch"):
            st.session_state["card_stock_id"] = stock_id
            st.switch_page("pages/2_個股分析卡.py")
    with b2:
        ask_ai = st.button("🤖 AI 解讀這檔股票", width="stretch")

    if ask_ai:
        try:
            from utils.ai import quick_analysis
        except Exception as e:
            st.error(f"AI 功能無法使用（需要 groq 套件與 GROQ_API_KEY）：{e}")
        else:
            trend = ""
            if not hist.empty:
                recent = hist.tail(10)
                trend = "、".join(f"{d[5:]}:{s}" for d, s in zip(recent["date"], recent["total_score"]))
            context = f"""
以下是 {row.get('stock_name', '')}（{stock_id}，{row.get('industry', '')}）在 {date} 的選股評分：
- 總分 {row['total_score']} / 100（{rank_text}）
- 籌碼 {row['chip_score']}/32、資金 {row['margin_score']}/8、基本面 {row['fund_score']}/35、技術 {row['tech_score']}/25
- 收盤 {row['close_price']}，近5日 {row['change_5d']}%，近20日 {row['change_20d']}%，20日均量 {row['avg_volume_20']} 張
- 加分理由：{'；'.join(row['reasons']) or '無'}
- 風險提醒：{'；'.join(row['warnings']) or '無'}
- 近 10 個評分日總分走勢：{trend or '無'}
"""
            question = (
                "請根據以上評分，用繁體中文說明：1) 這檔股票目前最主要的強項是什麼；"
                "2) 哪些面向偏弱或有風險；3) 分數走勢代表剛轉強還是已經強一段時間；"
                "4) 接下來該觀察哪些數據來確認訊號。請精簡，不要給買賣建議。"
            )
            with st.spinner("AI 分析中…"):
                st.markdown(quick_analysis(question, context))
            st.caption("⚠️ AI 解讀僅供參考，不構成投資建議。")

# =====================================================================
# 規則回測
# =====================================================================
with tab_backtest:
    st.markdown(
        "檢驗評分規則有沒有用：每個評分日買進排行榜前 N 名（等權重），持有 N 個交易日後的平均報酬，"
        "對照同一天所有列入排名股票的平均報酬。"
    )
    c1, c2 = st.columns(2)
    with c1:
        bt_top = st.selectbox("前幾名", [10, 20, 30, 50], index=1)
    with c2:
        bt_h = st.selectbox("持有幾個交易日", [5, 10, 20], index=0)

    try:
        perf = load_performance(bt_top, bt_h)
    except Exception:
        perf = None
        st.warning("回測資料暫時讀取失敗（資料庫查詢逾時），請稍後按「強制重新整理」或重新選一次條件。")
    if perf is None:
        pass
    elif perf.empty:
        st.info(f"評分資料還不夠長，需要至少 {bt_h + 1} 個評分日才能計算。")
    else:
        perf["excess"] = perf["top_return"] - perf["market_return"]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric(f"前 {bt_top} 名平均報酬", f"{perf['top_return'].mean():.2f}%")
        m2.metric("全市場平均報酬", f"{perf['market_return'].mean():.2f}%")
        m3.metric("平均超額報酬", f"{perf['excess'].mean():+.2f}%")
        m4.metric("勝過市場的天數", f"{(perf['excess'] > 0).mean():.0%}", f"共 {len(perf)} 個評分日",
                  delta_color="off")

        long = perf.melt(id_vars="date", value_vars=["top_return", "market_return"],
                         var_name="組合", value_name="報酬%")
        long["組合"] = long["組合"].map({"top_return": f"前 {bt_top} 名", "market_return": "全市場平均"})
        long["date"] = pd.to_datetime(long["date"])
        chart = alt.Chart(long).mark_line().encode(
            x=alt.X("date:T", title="評分日", axis=alt.Axis(format="%m/%d")),
            y=alt.Y("報酬%:Q", title=f"持有 {bt_h} 日報酬（%）"),
            color=alt.Color("組合:N", scale=alt.Scale(range=[UP_COLOR, "#64748b"])),
            tooltip=["date:T", "組合:N", "報酬%:Q"],
        ).properties(height=320)
        st.altair_chart(chart, width="stretch")

        bars = alt.Chart(perf.assign(date=pd.to_datetime(perf["date"]))).mark_bar().encode(
            x=alt.X("date:T", title="評分日", axis=alt.Axis(format="%m/%d")),
            y=alt.Y("excess:Q", title="超額報酬（%）"),
            color=alt.condition(alt.datum.excess > 0, alt.value(UP_COLOR), alt.value(DOWN_COLOR)),
            tooltip=["date:T", alt.Tooltip("excess:Q", format=".2f")],
        ).properties(height=200)
        st.altair_chart(bars, width="stretch")

        st.caption(
            "⚠️ 回測限制：報酬已用還原權值計算（除權息、減資、分割不會算成虧損），但未扣交易成本與稅；"
            "評分資料從 2026 年 6 月開始，樣本期短，結果只能當參考，不代表未來表現。"
        )

st.markdown("---")
st.caption("⚠️ 本系統資料僅供參考，不構成投資建議。投資有風險，請謹慎評估。")
