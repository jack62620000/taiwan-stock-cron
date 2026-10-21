import streamlit as st
import pandas as pd
import altair as alt
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.glossary import render_page_guide
from utils.db import (
    get_stock_list, get_signal_dates, get_signals,
    get_signal_history, get_signal_performance,
    get_rows_for_stocks, get_price_adjustments, apply_adjustments, get_momentum_latest,
)
from utils.watch import add_watch_form, add_watch_button
from utils.granville import STOP_RULE
from utils.momentum import BACKTEST_MD, RULES_MD, momentum_choices, momentum_table
from utils.analysis import (
    FOLLOW_DAYS, MOMENTUM_FOLLOW_PICKS, TRADE_COST_PCT, momentum_followup, render_followup, render_market_warning,
)
from utils.ui import require_login, render_metric_cards, style_sign, UP_COLOR, DOWN_COLOR
require_login()

st.set_page_config(page_title="每日選股訊號", page_icon="🎯", layout="wide")
st.title("🎯 每日選股訊號")
render_page_guide("signals")
st.caption(
    "每個交易日收盤後，依固定規則幫全市場股票打分數："
    "總分 100 = 籌碼 32 + 資金 8 + 基本面 35 + 技術面 25（規則 v3）。"
    "排名只列 20 日均量 500 張以上的股票。風險提醒不扣分，僅供參考。"
)

if st.button("🔄 強制重新整理資料"):
    st.cache_data.clear()
    get_stock_list.cache_clear()
    st.rerun()

render_market_warning()

DIMENSIONS = {
    "chip_score": "籌碼",
    "margin_score": "資金",
    "fund_score": "基本面",
    "tech_score": "技術",
}
HORIZONS = [5, 10, 20]  # 回測：評分隔天收盤買進後，持有幾個交易日的報酬
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
def load_momentum():
    df = get_momentum_latest()
    if df.empty:
        return df
    for c in ["close_price", "high_52w", "rs_rating", "vol_ratio", "atr14"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    info = get_stock_list(include_delisted=True)[["stock_id", "stock_name", "industry", "market"]]
    return df.merge(info, on="stock_id", how="left")

@st.cache_data(ttl=3600)
def load_momentum_followup():
    return momentum_followup()

@st.cache_data(ttl=3600)
def load_performance(top_n, horizon):
    return get_signal_performance(top_n, horizon)

@st.cache_data(ttl=3600)
def load_stock_backtest(stock_id):
    """
    單一股票每個評分日的總分、排名，以及評分隔天收盤買進、持有 5、10、20 個交易日後收盤賣出的報酬
    （%，還原權值，已扣交易成本）。分數收盤後才算得出來，評分當天的收盤價買不到，所以隔天才買
    """
    hist = get_signal_history(stock_id, limit=300)
    if hist.empty:
        return pd.DataFrame()
    prices = get_rows_for_stocks("daily_prices", [stock_id], since=hist["date"].min(),
                                 columns="stock_id, date, close_price")
    if prices.empty:
        return pd.DataFrame()
    prices = apply_adjustments(prices, get_price_adjustments(stock_id, prices["date"].min()))
    close = pd.to_numeric(prices.set_index("date")["close_price"], errors="coerce").dropna().sort_index()
    out = hist[["date", "total_score", "rank"]].copy()
    for col in ["total_score", "rank"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    pos = close.index.get_indexer(out["date"])  # 評分日在股價序列的位置，沒有股價為 -1
    for h in HORIZONS:
        out[f"ret_{h}"] = [
            (close.iloc[p + 1 + h] / close.iloc[p + 1] - 1) * 100 - TRADE_COST_PCT
            if 0 <= p and p + 1 + h < len(close) else None
            for p in pos
        ]
        out[f"ret_{h}"] = pd.to_numeric(out[f"ret_{h}"], errors="coerce")
    return out.reset_index(drop=True)

dates = load_dates()
if not dates:
    st.warning("還沒有評分資料。請到 GitHub Actions 執行「訊號評分回補」，或等今晚的每日排程跑完。")
    st.stop()

tab_rank, tab_momentum, tab_stock, tab_backtest = st.tabs(
    ["📋 今日排行榜", "🚀 動能選股", "📈 個股分數走勢", "🧪 規則回測"])

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
            "total_score": st.column_config.NumberColumn("總分", format="%d", help="滿分 100"),
            "chip_score": st.column_config.NumberColumn("籌碼", format="%d", help="滿分 32"),
            "margin_score": st.column_config.NumberColumn("資金", format="%d", help="滿分 8"),
            "fund_score": st.column_config.NumberColumn("基本面", format="%d", help="滿分 35"),
            "tech_score": st.column_config.NumberColumn("技術", format="%d", help="滿分 25"),
            "close_price": st.column_config.NumberColumn("收盤", format="%.2f"),
            "change_5d": st.column_config.NumberColumn("5日%", format="%.2f"),
            "change_20d": st.column_config.NumberColumn("20日%", format="%.2f"),
            "avg_volume_20": st.column_config.NumberColumn("20日均量(張)", format="%d"),
            "reasons": st.column_config.ListColumn("加分理由", width="large"),
            "warnings": st.column_config.ListColumn("風險提醒", width="medium"),
        },
    )

    add_watch_form(view, "每日選股", date, "picks")

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
| | RSI ≥ 70 | 4 |
| | 20 日漲幅為正 | 11 |

**規則 v4 調整了什麼**（2026-10-21，改用「隔天開盤買」驗證 2025-02～2026-10，比較接近實際操作）：
- 技術面「RSI 在 50～75」改成「RSI ≥ 70」：RSI 70 以上的股票之後反而比較強，前後兩段期間都一樣；50～75 是所有加分條件中效果最弱的。
- 每天總分前 20 名之後 20 個交易日比全體平均多賺：v3 4.49%，v4 4.83%（隔天開盤買）。

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
# 個股分數走勢
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
    with b2:
        add_watch_button(stock_id, row.get("stock_name", ""), "每日選股", date, "stock")
    with b1:
        if st.button("🗂️ 前往個股分析卡", width="stretch"):
            st.session_state["card_stock_id"] = stock_id
            st.switch_page("pages/2_個股分析卡.py")

# =====================================================================
# 規則回測（單一個股）
# =====================================================================
def mean_text(s):
    s = s.dropna()
    return (f"{s.mean():+.2f}%", f"{len(s)} 次", s.mean()) if len(s) else ("—", "沒有樣本", None)


with tab_backtest:
    st.markdown(
        "看單一檔股票過去每個評分日的總分、排名，以及之後 5、10、20 個交易日的漲跌幅，"
        "檢查這檔股票「排名前面的時候」之後是不是真的比較會漲。"
    )
    c1, c2, c3 = st.columns([2, 1, 1])
    bt_selected = c1.selectbox("選擇股票（排行榜上的股票排在前面）", options, index=options.index(selected),
                               key="bt_stock")
    bt_id, bt_name = (bt_selected.split(" ", 1) + [""])[:2]
    bt_top = c2.selectbox("算「前幾名」", [10, 20, 30, 50], index=1)
    bt_h = c3.selectbox("持有幾個交易日", HORIZONS, index=1)

    bt = load_stock_backtest(bt_id)
    if bt.empty:
        st.info("這檔股票沒有評分紀錄或股價資料。")
    else:
        ret = f"ret_{bt_h}"
        in_top = bt["rank"].notna() & (bt["rank"] <= bt_top)
        top_text, top_n_text, top_sign = mean_text(bt.loc[in_top, ret])
        other_text, other_n_text, other_sign = mean_text(bt.loc[~in_top, ret])
        done = bt[ret].dropna()
        win = (bt.loc[in_top, ret].dropna() > 0).mean() if in_top.any() else None
        render_metric_cards([
            ("評分日數", f"{len(bt)} 天", f"{bt['date'].min()} 起", None),
            (f"進前 {bt_top} 名", f"{int(in_top.sum())} 次", f"佔 {in_top.mean():.0%}", None),
            (f"前 {bt_top} 名時，持有 {bt_h} 日平均", top_text, top_n_text, top_sign),
            (f"其他時候，持有 {bt_h} 日平均", other_text, other_n_text, other_sign),
            (f"前 {bt_top} 名時上漲比例", f"{win:.0%}" if win is not None and pd.notna(win) else "—",
             f"全部評分日 {(done > 0).mean():.0%}" if len(done) else None, None),
        ])

        # 依總分分組：分數越高，之後是不是越好
        groups = pd.cut(bt["total_score"], [-1, 49, 69, 100], labels=["50 分以下", "50～69 分", "70 分以上"])
        grouped = bt.groupby(groups, observed=False)
        score_table = grouped[[f"ret_{h}" for h in HORIZONS]].mean()
        score_table.columns = [f"持有{h}日平均%" for h in HORIZONS]
        score_table.insert(0, "評分日數", grouped["total_score"].count())
        score_table = score_table.rename_axis("總分").reset_index()
        score_table["總分"] = score_table["總分"].astype(str)
        left, right = st.columns([2, 3])
        with left:
            st.markdown("**依總分分組的平均漲跌幅**")
            st.dataframe(
                score_table.style.format("{:+.2f}", subset=[f"持有{h}日平均%" for h in HORIZONS], na_rep="—")
                    .map(style_sign, subset=[f"持有{h}日平均%" for h in HORIZONS]),
                hide_index=True, width="stretch",
            )
            add_watch_button(bt_id, bt_name, "每日選股回測", dates[0], "backtest")
        with right:
            pts = bt.dropna(subset=[ret]).assign(
                組別=lambda d: (d["rank"].notna() & (d["rank"] <= bt_top)).map({True: f"前 {bt_top} 名", False: "其他"}))
            if pts.empty:
                st.info(f"評分日之後還沒滿 {bt_h + 1} 個交易日，暫時算不出報酬。")
            else:
                st.altair_chart(alt.Chart(pts).mark_circle(size=60, opacity=0.75).encode(
                    x=alt.X("total_score:Q", title="當天總分", scale=alt.Scale(zero=False)),
                    y=alt.Y(f"{ret}:Q", title=f"持有 {bt_h} 日報酬（%，扣成本）"),
                    color=alt.Color("組別:N", scale=alt.Scale(domain=[f"前 {bt_top} 名", "其他"],
                                                             range=[UP_COLOR, "#94a3b8"]),
                                    legend=alt.Legend(orient="top", title=None)),
                    tooltip=[alt.Tooltip("date:N", title="評分日"), alt.Tooltip("total_score:Q", title="總分"),
                             alt.Tooltip("rank:Q", title="排名"), alt.Tooltip(f"{ret}:Q", title="漲跌幅%", format="+.2f")],
                ).properties(height=300), width="stretch")

        with st.expander("📄 每個評分日的紀錄"):
            rec = bt.sort_values("date", ascending=False)[
                ["date", "total_score", "rank"] + [f"ret_{h}" for h in HORIZONS]]
            rec.columns = ["評分日", "總分", "排名"] + [f"持有{h}日%" for h in HORIZONS]
            st.dataframe(
                rec.style.format("{:.0f}", subset=["總分", "排名"], na_rep="—")
                    .format("{:+.2f}", subset=[f"持有{h}日%" for h in HORIZONS], na_rep="")
                    .map(style_sign, subset=[f"持有{h}日%" for h in HORIZONS]),
                hide_index=True, width="stretch", height=min(42 + 35 * len(rec), 500),
            )
        st.caption(
            f"⚠️ 分數收盤後才算得出來，所以用「評分隔天收盤買進、持有 N 個交易日後收盤賣出」計算（還原權值），"
            f"已扣交易成本約 {TRADE_COST_PCT:g}%（手續費買賣各 0.1425%＋證交稅 0.3%）；"
            "排名只排 20 日均量 500 張以上的股票。單一個股樣本少，結果只能當參考，不代表未來表現。"
        )

    with st.expander(f"📊 整體規則成效：每天買前 {bt_top} 名 vs 全市場平均"):
        st.caption(f"每個評分日隔天收盤買進前 {bt_top} 名、持有 {bt_h} 個交易日後收盤賣出，"
                   f"報酬已扣交易成本約 {TRADE_COST_PCT:g}%；全市場平均是同一段時間 20 日均量夠的股票平均漲跌（不扣成本），"
                   "超額報酬為正，才代表照排名買比抱著大盤好。")
        try:
            perf = load_performance(bt_top, bt_h)
        except Exception:
            perf = None
            st.warning("整體回測資料暫時讀取失敗（資料庫查詢逾時），請稍後按「強制重新整理」再看一次。")
        if perf is not None and perf.empty:
            st.info(f"評分資料還不夠長，需要至少 {bt_h + 2} 個評分日才能計算。")
        elif perf is not None:
            # 買前幾名要付交易成本；全市場平均當作「什麼都不做、抱著大盤」的對照，不扣成本
            perf["top_return"] = perf["top_return"] - TRADE_COST_PCT
            perf["excess"] = perf["top_return"] - perf["market_return"]
            m1, m2, m3, m4 = st.columns(4)
            m1.metric(f"前 {bt_top} 名平均報酬（扣成本）", f"{perf['top_return'].mean():.2f}%")
            m2.metric("全市場平均報酬", f"{perf['market_return'].mean():.2f}%")
            m3.metric("平均超額報酬", f"{perf['excess'].mean():+.2f}%")
            m4.metric("勝過市場的天數", f"{(perf['excess'] > 0).mean():.0%}", f"共 {len(perf)} 個評分日",
                      delta_color="off")
            bars = alt.Chart(perf.assign(date=pd.to_datetime(perf["date"]))).mark_bar().encode(
                x=alt.X("date:T", title="評分日", axis=alt.Axis(format="%m/%d")),
                y=alt.Y("excess:Q", title="超額報酬（%）"),
                color=alt.condition(alt.datum.excess > 0, alt.value(UP_COLOR), alt.value(DOWN_COLOR)),
                tooltip=["date:T", alt.Tooltip("excess:Q", format=".2f")],
            ).properties(height=200)
            st.altair_chart(bars, width="stretch")

# =====================================================================
# 動能選股：趨勢模板（Minervini）、52 週新高突破＋量增
# =====================================================================
with tab_momentum:
    mom = load_momentum()
    if mom.empty:
        st.info("還沒有動能選股資料，等今晚的每日排程跑完就會出現。")
    else:
        m_date = mom["date"].max()
        liq = mom[mom["liquid"]]
        st.caption(f"資料日期：{m_date}（收盤後）。只判斷 20 日均量 500 張以上的股票，共 {len(liq):,} 檔；"
                   "條件說明與回測結果在下方。")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("兩者同時", f"{int((liq['trend_template'] & liq['breakout']).sum())} 檔")
        m2.metric("52 週新高＋量增", f"{int(liq['breakout'].sum())} 檔")
        m3.metric("符合趨勢模板", f"{int(liq['trend_template'].sum())} 檔")
        m4.metric("今天剛符合趨勢模板", f"{int(liq['trend_new'].sum())} 檔")
        choices = momentum_choices(liq)
        pick = st.radio("顯示", list(choices), horizontal=True, key="momentum_pick",
                        help="回測以「兩者同時」最好；趨勢模板（全部）每天都很多檔，適合當作篩選的第一步")
        view = liq[choices[pick]].copy()
        if view.empty:
            st.info(f"{m_date} 沒有「{pick}」的股票。")
        else:
            view = momentum_table(view)
            table = view[["stock_id", "stock_name", "industry", "close_price", "rs_rating", "vol_ratio",
                          "off_high", "tt", "bo", "stop", "risk"]]
            st.dataframe(
                table, hide_index=True, width="stretch", height=min(42 + 35 * len(table), 600),
                column_config={
                    "stock_id": st.column_config.TextColumn("代號", width="small", pinned=True),
                    "stock_name": st.column_config.TextColumn("名稱", width="small", pinned=True),
                    "industry": st.column_config.TextColumn("產業", width="small"),
                    "close_price": st.column_config.NumberColumn("收盤", format="%.2f"),
                    "rs_rating": st.column_config.NumberColumn("RS", format="%d",
                                                               help="相對強度 0～99：贏過多少 % 的股票"),
                    "vol_ratio": st.column_config.NumberColumn("量比", format="%.2f",
                                                               help="今天成交量 ÷ 前 50 日均量"),
                    "off_high": st.column_config.NumberColumn("離52週高%", format="%.1f",
                                                              help="0 代表今天就是 52 週新高"),
                    "tt": st.column_config.TextColumn("趨勢模板", width="small"),
                    "bo": st.column_config.TextColumn("新高突破", width="small"),
                    "stop": st.column_config.NumberColumn("停損價", format="%.2f", help=STOP_RULE),
                    "risk": st.column_config.NumberColumn("每張風險", format="%d",
                                                          help="（收盤 − 停損價）× 1000 股，單位：元"),
                },
            )
            add_watch_form(view, "動能選股", m_date, "momentum")
        with st.expander("📖 條件說明與回測結果"):
            st.markdown(RULES_MD)
            st.markdown(BACKTEST_MD)

    # ===== 事後追蹤：最近出現過的動能訊號，之後實際漲跌多少 =====
    st.subheader("🧾 事後追蹤")
    st.caption(f"最近 {FOLLOW_DAYS} 個交易日出現過的動能訊號，之後實際漲跌多少，用來看最近的訊號準不準。"
               "回測看的是 20、60 日，幾天內的漲跌只能當參考。")
    try:
        f_date, follow = load_momentum_followup()
    except Exception:
        follow = None
        st.warning("事後追蹤資料暫時讀取失敗，請稍後按「強制重新整理」再看一次。")
    if follow is not None and follow.empty:
        st.info(f"最近 {FOLLOW_DAYS} 個交易日還沒有動能訊號紀錄（訊號紀錄從開始保留那天起慢慢累積）。")
    elif follow is not None:
        f_pick = st.radio("訊號", MOMENTUM_FOLLOW_PICKS, horizontal=True, key="momentum_follow_pick")
        f_view = follow[follow["signal"].str.contains(f_pick, regex=False)]
        if f_view.empty:
            st.info(f"最近 {FOLLOW_DAYS} 個交易日沒有「{f_pick}」的訊號。")
        else:
            st.caption(f"資料日期：{f_date}（收盤後），「{f_pick}」共 {len(f_view)} 個訊號。")
            render_followup(f_view, {"signal": st.column_config.TextColumn("訊號", width="medium")})

st.markdown("---")
st.caption("⚠️ 本系統資料僅供參考，不構成投資建議。投資有風險，請謹慎評估。")
