import streamlit as st
import pandas as pd
import altair as alt
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.glossary import render_page_guide
from utils.db import (
    get_stock_list,
    get_watchlist, add_to_watchlist, add_many_to_watchlist, update_watchlist_note, remove_from_watchlist,
)
from utils.granville import STOP_RULE, CHOP_DAYS, CHOP_CROSSES
from utils.analysis import granville_status, load_watch_inputs
from utils.paper import add_form
from utils.groups import group_picker, group_manager, parse_stock_codes
from utils.ui import require_login, style_sign, go_picks_tab
require_login()

st.set_page_config(page_title="自選股", page_icon="⭐", layout="wide")
st.title("⭐ 自選股")
render_page_guide("watchlist")
st.caption("把常看的股票加進來，一頁看完最新股價、法人動向與營收，並分別用均線買賣點與評分排行分析。"
           "清單存在資料庫，換裝置也看得到；每個人的帳號底下可以分成多個分組。")

if st.button("🔄 強制重新整理資料"):
    st.cache_data.clear()
    get_stock_list.cache_clear()
    st.rerun()

stock_list = get_stock_list()
names = dict(zip(stock_list["stock_id"], stock_list["stock_name"].fillna(""))) if not stock_list.empty else {}
pick_col, _ = st.columns([1, 2])
with pick_col:
    group_id, group_name = group_picker()
group_manager()
watch = get_watchlist(group_id)
watch_ids = watch["stock_id"].tolist()


# ===== 載入自選股資料 =====
SCORE_DAYS = 60   # 每日選股分數走勢看最近幾個評分日
RECENT_DAYS = 20  # 「近期」統計用最近幾個評分日
TOP_N = 20        # 「進前幾名」


@st.cache_data(ttl=3600)
def load_watch_data(ids):
    """自選股最近的評分、股價、法人與營收（和 LINE 推播共用 utils.analysis.load_watch_inputs）。ids 是 tuple，清單改變就重新載入"""
    return load_watch_inputs(list(ids), SCORE_DAYS)


def build_table(ids, sig_dates, signals, prices, ins, revenue):
    rows = []
    for sid in ids:
        r = {"代號": sid, "名稱": names.get(sid, "")}
        s = signals[signals["stock_id"] == sid].sort_values("date") if not signals.empty else signals
        if not s.empty:
            now = s.iloc[-1]
            r["評分"] = now["total_score"]
            r["分數變化"] = now["total_score"] - s.iloc[-2]["total_score"] if len(s) > 1 else None
            r["排名"] = now["rank"]
            r["5日%"] = now["change_5d"]
            r["20日%"] = now["change_20d"]
        p = prices[prices["stock_id"] == sid].sort_values("date") if not prices.empty else prices
        if not p.empty:
            close = pd.to_numeric(p["close_price"], errors="coerce").dropna()
            if not close.empty:
                r["收盤價"] = close.iloc[-1]
                r["漲跌幅%"] = (close.iloc[-1] / close.iloc[-2] - 1) * 100 if len(close) > 1 else None
        i = ins[ins["stock_id"] == sid].sort_values("date") if not ins.empty else ins
        if not i.empty:
            r["外資5日(張)"] = pd.to_numeric(i["foreign_net_shares"], errors="coerce").tail(5).sum() / 1000
            r["投信5日(張)"] = pd.to_numeric(i["sitca_net_shares"], errors="coerce").tail(5).sum() / 1000
            hold = pd.to_numeric(i["foreign_hold_ratio"], errors="coerce").dropna()
            if not hold.empty:
                r["外資持股%"] = hold.iloc[-1]
                r["持股20日變化"] = hold.iloc[-1] - hold.iloc[-21] if len(hold) > 20 else None
        v = revenue[revenue["stock_id"] == sid].sort_values("year_month") if not revenue.empty else revenue
        if not v.empty:
            r["營收年增%"] = v.iloc[-1]["yoy_growth"]
        rows.append(r)
    cols = ["代號", "名稱", "收盤價", "漲跌幅%", "5日%", "20日%", "評分", "分數變化", "排名",
            "外資5日(張)", "投信5日(張)", "外資持股%", "持股20日變化", "營收年增%"]
    df = pd.DataFrame(rows).reindex(columns=cols)
    for c in cols[2:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


@st.cache_data(ttl=3600)
def granville_table(ids, prices, ins):
    return granville_status(ids, prices, ins, names)


def picks_table(ids, signals):
    """每檔自選股最新的評分排行，以及最近 RECENT_DAYS 個評分日的平均分、進前 TOP_N 名的次數"""
    rows = []
    for sid in ids:
        s = signals[signals["stock_id"] == sid].sort_values("date") if not signals.empty else signals
        r = {"代號": sid, "名稱": names.get(sid, "")}
        if not s.empty:
            now = s.iloc[-1]
            recent = s.tail(RECENT_DAYS)
            ranks = pd.to_numeric(recent["rank"], errors="coerce")
            r.update({
                "總分": now["total_score"], "排名": now["rank"],
                "籌碼": now["chip_score"], "資金": now["margin_score"],
                "基本面": now["fund_score"], "技術": now["tech_score"],
                f"近{RECENT_DAYS}日平均": pd.to_numeric(recent["total_score"], errors="coerce").mean(),
                f"進前{TOP_N}名": int((ranks <= TOP_N).sum()),
                "加分理由": now["reasons"] if isinstance(now["reasons"], list) else [],
                "風險提醒": now["warnings"] if isinstance(now["warnings"], list) else [],
            })
        rows.append(r)
    cols = ["代號", "名稱", "總分", "排名", f"近{RECENT_DAYS}日平均", f"進前{TOP_N}名",
            "籌碼", "資金", "基本面", "技術", "加分理由", "風險提醒"]
    df = pd.DataFrame(rows).reindex(columns=cols)
    for c in cols[2:10]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ===== 自選股總表 =====
if not watch_ids:
    st.info("還沒有自選股，用下方「新增自選股」加入，或在個股分析卡、選股頁按「⭐ 加入自選股」。")
else:
    sig_dates, signals, prices, ins, revenue = load_watch_data(tuple(watch_ids))
    table = build_table(watch_ids, sig_dates, signals, prices, ins, revenue)
    st.subheader(f"{group_name}：{len(watch_ids)} 檔（評分日 {sig_dates[0] if sig_dates else '—'}）")
    tab_all, tab_granville, tab_picks = st.tabs(["📋 總覽", "📐 均線買賣點", "🏆 評分排行"])

    with tab_all:
        sign_cols = ["漲跌幅%", "5日%", "20日%", "分數變化", "外資5日(張)", "投信5日(張)", "持股20日變化", "營收年增%"]
        styler = table.style \
            .format("{:,.2f}", subset=["收盤價", "漲跌幅%", "5日%", "20日%", "外資持股%", "持股20日變化", "營收年增%"], na_rep="") \
            .format("{:,.0f}", subset=["評分", "排名", "外資5日(張)", "投信5日(張)"], na_rep="") \
            .format("{:+,.0f}", subset=["分數變化"], na_rep="") \
            .map(style_sign, subset=sign_cols)
        st.dataframe(
            styler, hide_index=True, width="stretch",
            column_config={"代號": st.column_config.TextColumn(pinned=True),
                           "名稱": st.column_config.TextColumn(pinned=True)},
        )
        st.caption("漲跌幅用還原權值後的收盤價計算；排名只排 20 日均量 500 張以上的股票，冷門股沒有排名。"
                   "持股20日變化的單位是百分點。")

        # 加入模擬持股：以最新收盤價買進
        if not prices.empty:
            latest_price_date = prices["date"].max()
            add_form(table.rename(columns={"代號": "stock_id", "名稱": "stock_name", "收盤價": "close_price"}),
                     "自選股", latest_price_date, "watchlist")

        # 跳到個股分析卡
        jump_col, _ = st.columns([2, 3])
        with jump_col:
            pick = st.selectbox("查看個股分析卡", watch_ids, format_func=lambda s: f"{s} {names.get(s, '')}")
            if st.button("前往個股分析卡"):
                st.session_state["card_stock_id"] = pick
                st.switch_page("pages/2_個股分析卡.py")

    with tab_granville:
        g = granville_table(tuple(watch_ids), prices, ins) if not prices.empty else pd.DataFrame()
        if g.empty:
            st.info("股價資料不足，暫時無法判斷葛蘭碧法則（新上市的股票要有 20 個交易日以上的資料）。")
        else:
            st.caption(f"資料日期：{prices['date'].max()}（收盤後），依 20 日均線、「均線買賣點」的預設參數。"
                       "觸發價是推算「下一個交易日收盤」要到多少才會出現訊號；"
                       f"停損價是{STOP_RULE}；每張風險是買在預計價、跌到停損價出場，一張會虧多少元。")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("明天開盤可買", f"{(g['狀態'] == '✅ 明天開盤可買').sum()} 檔")
            m2.metric("等待法則2", f"{(g['狀態'] == '⏳ 等待法則2').sum()} 檔")
            m3.metric("今天出現賣出訊號", f"{(g['狀態'] == '⚠️ 今天出現賣出訊號').sum()} 檔")
            m4.metric("過熱（法則8）", f"{(g['狀態'] == '🔥 過熱（法則8）').sum()} 檔")
            st.dataframe(
                g.style
                    .format("{:,.2f}", subset=["收盤價", "均線", "買進觸發價", "停損價", "賣出觸發價"], na_rep="")
                    .format("{:,.0f}", subset=["每張風險"], na_rep="")
                    .format("{:+.2f}", subset=["乖離%", "賣出距離%"], na_rep="")
                    .map(style_sign, subset=["乖離%", "賣出距離%"]),
                hide_index=True, width="stretch", height=min(42 + 35 * len(g), 600),
                column_config={
                    "代號": st.column_config.TextColumn(pinned=True),
                    "名稱": st.column_config.TextColumn(pinned=True),
                    "乖離%": st.column_config.Column(help="收盤價離均線幾 %"),
                    "買進觸發價": st.column_config.Column(help="明天收盤 ≥ 這個價就出現法則 2（買點）"),
                    "停損價": st.column_config.Column(help=STOP_RULE + "，收盤跌破就出場"),
                    "每張風險": st.column_config.Column(help="（預計買進價 − 停損價）× 1000 股，單位：元"),
                    "賣出觸發價": st.column_config.Column(
                        help="明天收盤 ≤ 這個價就出現法則 5 或 6（賣出訊號）；空白代表明天漲跌停內都不會出現"),
                    "賣出距離%": st.column_config.Column(help="賣出觸發價比今天收盤低（−）或高（+）幾 %"),
                    "趨勢": st.column_config.Column(
                        help=f"近 {CHOP_DAYS} 個交易日穿越均線 {CHOP_CROSSES} 次以上標成盤整，只當參考"),
                    "說明": st.column_config.TextColumn(width="large"),
                },
            )
            go_picks_tab("到「均線買賣點」看走勢圖與完整說明", "📐 均線買賣點", "watch_go_granville", icon="📐")

    with tab_picks:
        if signals.empty:
            st.info("自選股還沒有評分資料。")
        else:
            pt = picks_table(watch_ids, signals)
            st.caption(f"最新評分日 {sig_dates[0]}。總分 100 = 籌碼 32 + 資金 8 + 基本面 35 + 技術 25；"
                       f"近{RECENT_DAYS}日平均、進前{TOP_N}名是最近 {RECENT_DAYS} 個評分日的統計。")
            st.dataframe(
                pt.style.format("{:,.0f}", subset=["總分", "排名", "籌碼", "資金", "基本面", "技術"], na_rep="")
                    .format("{:,.1f}", subset=[f"近{RECENT_DAYS}日平均"], na_rep="")
                    .format("{:,.0f}", subset=[f"進前{TOP_N}名"], na_rep=""),
                hide_index=True, width="stretch", height=min(42 + 35 * len(pt), 600),
                column_config={
                    "代號": st.column_config.TextColumn(pinned=True),
                    "名稱": st.column_config.TextColumn(pinned=True),
                    f"進前{TOP_N}名": st.column_config.Column(help=f"最近 {RECENT_DAYS} 個評分日裡排進前 {TOP_N} 名的天數"),
                    "加分理由": st.column_config.ListColumn(width="large"),
                    "風險提醒": st.column_config.ListColumn(width="medium"),
                },
            )
            trend = signals.assign(
                股票=signals["stock_id"] + " " + signals["stock_id"].map(names).fillna(""),
                date=pd.to_datetime(signals["date"]),
                total_score=pd.to_numeric(signals["total_score"], errors="coerce"),
            )
            focus = alt.selection_point(fields=["股票"], bind="legend")
            st.markdown(f"**最近 {SCORE_DAYS} 個評分日的總分走勢**（點圖例可以只看一檔）")
            st.altair_chart(alt.Chart(trend).mark_line(point=alt.OverlayMarkDef(size=15)).encode(
                x=alt.X("date:T", title=None, axis=alt.Axis(format="%m/%d")),
                y=alt.Y("total_score:Q", title="總分", scale=alt.Scale(domain=[0, 100])),
                color=alt.Color("股票:N", legend=alt.Legend(orient="top", title=None)),
                opacity=alt.condition(focus, alt.value(1), alt.value(0.15)),
                tooltip=[alt.Tooltip("date:T", title="日期"), "股票:N", alt.Tooltip("total_score:Q", title="總分")],
            ).add_params(focus).properties(height=320), width="stretch")
            go_picks_tab("到「評分排行」看單一個股的規則回測", "🏆 評分排行", "watch_go_signals", icon="🏆")

st.markdown("---")

# ===== 管理自選股 =====
st.subheader("⚙️ 管理自選股")
add_col, remove_col = st.columns(2)

with add_col:
    st.markdown("**新增自選股**")
    candidates = [s for s in stock_list["stock_id"].tolist() if s not in watch_ids] if not stock_list.empty else []
    new_id = st.selectbox("股票（輸入代號或名稱）", candidates, index=None, placeholder="例如: 2330 或 台積電",
                          format_func=lambda s: f"{s} {names.get(s, '')}")
    new_note = st.text_input("備註（選填）", placeholder="例如: 等回測月線再買")
    if st.button("➕ 加入", disabled=new_id is None):
        add_to_watchlist(group_id, new_id, new_note.strip())
        st.cache_data.clear()
        st.rerun()
    batch = st.text_area("一次加多檔（貼上代號或名稱，用逗號、空白或換行隔開）", key="watch_batch",
                         placeholder="例如: 2330, 2317, 2454", height=80)
    batch_add, batch_already, batch_unknown = parse_stock_codes(batch, names, watch_ids)
    if batch.strip():
        notes = []
        if batch_add:
            notes.append(f"會加入 {len(batch_add)} 檔：" + "、".join(f"{s} {names.get(s, '')}" for s in batch_add))
        if batch_already:
            notes.append("已經在清單：" + "、".join(batch_already))
        if batch_unknown:
            notes.append("找不到：" + "、".join(batch_unknown))
        st.caption("；".join(notes))
    if st.button(f"➕ 全部加入（{len(batch_add)} 檔）", disabled=not batch_add):
        add_many_to_watchlist(group_id, batch_add)
        st.session_state.pop("watch_batch", None)
        st.cache_data.clear()
        st.rerun()

with remove_col:
    st.markdown("**移除自選股**")
    to_remove = st.multiselect("選擇要移除的股票", watch_ids, format_func=lambda s: f"{s} {names.get(s, '')}")
    if st.button("🗑️ 移除", disabled=not to_remove):
        for sid in to_remove:
            remove_from_watchlist(group_id, sid)
        st.cache_data.clear()
        st.rerun()

if watch_ids:
    st.markdown("**編輯備註**")
    notes = watch.assign(名稱=watch["stock_id"].map(names),
                         加入日期=pd.to_datetime(watch["added_at"], format="ISO8601").dt.tz_convert("Asia/Taipei").dt.strftime("%Y-%m-%d"))
    notes = notes.rename(columns={"stock_id": "代號", "note": "備註"})[["代號", "名稱", "加入日期", "備註"]]
    edited = st.data_editor(notes, hide_index=True, width="stretch",
                            disabled=["代號", "名稱", "加入日期"], key="watch_notes")
    changed = edited[edited["備註"].fillna("") != notes["備註"].fillna("")]
    if st.button("💾 儲存備註", disabled=changed.empty):
        for _, r in changed.iterrows():
            update_watchlist_note(group_id, r["代號"], (r["備註"] or "").strip())
        st.success(f"已儲存 {len(changed)} 筆備註")
        st.rerun()
