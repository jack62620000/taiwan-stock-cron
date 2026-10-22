import streamlit as st
import pandas as pd
import sys
import os
from datetime import date, timedelta

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.glossary import render_page_guide
from utils.db import (
    get_stock_list, get_signal_dates, get_top_ranks, get_market_index, get_momentum_latest,
    get_rows_for_stocks, get_price_adjustments, apply_adjustments,
)
from utils.analysis import MARKET_MA, TRADE_COST_PCT
from utils.picks import (
    BACKTEST_MD, HOLD, RULES_MD, SLOTS, START, TOP_N,
    market_ok_by_date, replay, stock_tags, trade_returns,
)
from utils.watch import add_watch_form
from utils.ui import require_login, render_metric_cards, style_sign
require_login()

st.set_page_config(page_title="選股", page_icon="🧭", layout="wide")
st.title("🧭 選股")
render_page_guide("picks")

if st.button("🔄 強制重新整理資料"):
    st.cache_data.clear()
    get_stock_list.cache_clear()
    st.rerun()


# ===== 載入資料 =====
@st.cache_data(ttl=3600)
def load_plan():
    """照規則從 START 模擬到最新評分日：每筆交易（含報酬）、明天要做的事、最新評分日前 N 名"""
    dates = sorted(d for d in get_signal_dates(n=250) if d >= START)
    if not dates:
        return None
    ranks = get_top_ranks(dates[0], TOP_N)
    tops = {d: g.sort_values("rank")["stock_id"].tolist() for d, g in ranks.groupby("date")} if not ranks.empty else {}
    market_ok = market_ok_by_date(get_market_index("TAIEX", limit=250), MARKET_MA)
    trades, plan = replay(dates, tops, market_ok)
    ids = sorted({t["stock_id"] for t in trades} | set(tops.get(dates[-1], [])))
    prices = get_rows_for_stocks("daily_prices", ids, since=dates[0], columns="stock_id, date, open_price, close_price")
    if not prices.empty:
        prices = apply_adjustments(prices, get_price_adjustments(None, dates[0]))
    else:
        prices = pd.DataFrame(columns=["stock_id", "date", "open_price", "close_price"])
    today = ranks[ranks["date"] == dates[-1]] if not ranks.empty else pd.DataFrame()
    return dates, trade_returns(trades, prices, TRADE_COST_PCT), plan, today


@st.cache_data(ttl=3600)
def load_tags(ids):
    """參考標籤：動能（最新一天）、布林收斂、葛蘭碧（約 200 個日曆天的還原權值收盤價）"""
    ids = list(ids)
    since = (date.today() - timedelta(days=200)).isoformat()
    prices = get_rows_for_stocks("daily_prices", ids, since=since, columns="stock_id, date, close_price")
    if not prices.empty:
        prices = apply_adjustments(prices, get_price_adjustments(None, since))
    return stock_tags(ids, prices, get_momentum_latest())


@st.cache_data(ttl=3600)
def load_names():
    info = get_stock_list(include_delisted=True)
    if info.empty:
        return {}, {}
    return dict(zip(info["stock_id"], info["stock_name"].fillna(""))), dict(zip(info["stock_id"], info["industry"].fillna("")))


loaded = load_plan()
if loaded is None:
    st.warning("還沒有評分資料。等今晚的每日排程跑完就會出現。")
    st.stop()
dates, trades, plan, today = loaded
last = dates[-1]
names, industries = load_names()
open_trades = trades[trades["sell_date"].isna()]
done = trades[trades["sell_date"].notna()]
tag_ids = tuple(sorted(set(open_trades["stock_id"]) | set(today["stock_id"] if not today.empty else [])))
tags = load_tags(tag_ids) if tag_ids else {}


def tag_text(sid):
    return "　".join(tags.get(sid, []))


# ===== 大盤狀態 =====
if plan["market_ok"]:
    st.success(f"🟢 {last} 加權指數在 {MARKET_MA} 日均線（季線）之上：可以照規則買進。")
else:
    st.error(f"🔴 {last} 加權指數在 {MARKET_MA} 日均線（季線）之下：明天不買新股票，手上的照時間賣出。")

sell_ids = [p["stock_id"] for p in plan["sell"]]
rebuy = set(sell_ids) & set(plan["buy"])
render_metric_cards([
    ("明天開盤賣出", f"{len(sell_ids)} 檔", f"持有滿 {HOLD} 個交易日", None),
    ("明天開盤買進", f"{len(plan['buy'])} 檔", f"空出 {plan['free']} 個名額" if plan["market_ok"] else "大盤在季線下，不買", None),
    ("持有中", f"{len(plan['hold'])} / {SLOTS} 檔", "明天賣出的不算", None),
    ("已完成交易", f"{len(done)} 筆",
     f"平均 {done['ret'].mean():+.2f}%，勝率 {(done['ret'] > 0).mean():.0%}" if done["ret"].notna().any() else "還沒有",
     done["ret"].mean() if done["ret"].notna().any() else None),
])
st.caption(f"資料日期：{last}（收盤後）。照規則從 {START} 開始模擬，每檔放總資金的 1/{SLOTS}；"
           f"報酬用還原權值價格計算，已扣交易成本約 {TRADE_COST_PCT:g}%。")

tab_todo, tab_hold, tab_done, tab_rules, tab_more = st.tabs(
    ["🛒 明天要做的事", "📦 持有中", "📒 過去交易", "📖 規則與回測", "🔎 詳細分析"])

pct = st.column_config.NumberColumn
with tab_todo:
    st.markdown(f"#### 🟢 明天開盤賣出（{len(sell_ids)} 檔）")
    if not sell_ids:
        st.info("明天沒有要賣的股票。")
    else:
        rows = open_trades[open_trades["stock_id"].isin(sell_ids)]
        st.dataframe(pd.DataFrame({
            "代號": rows["stock_id"], "名稱": rows["stock_id"].map(names), "買進日": rows["buy_date"],
            "買進價": rows["buy_price"], "最新收盤": rows["now_price"], "至今%": rows["ret"],
            "說明": rows["stock_id"].map(lambda s: "明天也在買進名單：可以直接續抱，省一次交易成本" if s in rebuy else "持有滿 10 天，開盤賣出"),
        }).style.format("{:,.2f}", subset=["買進價", "最新收盤"], na_rep="—")
            .format("{:+.2f}", subset=["至今%"], na_rep="—").map(style_sign, subset=["至今%"]),
            hide_index=True, width="stretch")

    st.markdown(f"#### 🔴 {last} 每日選股前 {TOP_N} 名")
    if today.empty:
        st.info(f"{last} 沒有排名資料。")
    else:
        held = {p["stock_id"] for p in plan["hold"]}

        def action(sid):
            if sid in plan["buy"]:
                return "✅ 明天開盤買" if sid not in rebuy else "🔁 續抱（不用賣）"
            if sid in held:
                return "📦 已持有"
            return "⏸️ 大盤在季線下" if not plan["market_ok"] else "⏸️ 名額已滿"

        view = pd.DataFrame({
            "動作": today["stock_id"].map(action), "排名": today["rank"], "代號": today["stock_id"],
            "名稱": today["stock_id"].map(names), "產業": today["stock_id"].map(industries),
            "總分": today["total_score"], "參考標籤": today["stock_id"].map(tag_text),
        })
        st.dataframe(view, hide_index=True, width="stretch",
                     column_config={"排名": pct(format="%d"), "總分": pct(format="%d", help="滿分 100（規則 v5）"),
                                    "參考標籤": st.column_config.TextColumn(width="large")})
        st.caption("同一檔已經持有就不重複買；名額滿了不買。標籤只是參考，不影響買賣。")
        add_watch_form(today.assign(stock_name=today["stock_id"].map(names)), "選股", last, "picks_page")

with tab_hold:
    if open_trades.empty:
        st.info("目前沒有持股。")
    else:
        h = open_trades.sort_values("buy_date")
        st.dataframe(pd.DataFrame({
            "代號": h["stock_id"], "名稱": h["stock_id"].map(names), "買進日": h["buy_date"],
            "持有": h["day"].map(lambda d: f"第 {int(d)} / {HOLD} 天"),
            "賣出": h["day"].map(lambda d: "明天開盤" if d >= HOLD else f"再 {HOLD - int(d)} 個交易日後開盤"),
            "買進價": h["buy_price"], "最新收盤": h["now_price"], "至今%": h["ret"],
            "參考標籤": h["stock_id"].map(tag_text),
        }).style.format("{:,.2f}", subset=["買進價", "最新收盤"], na_rep="—")
            .format("{:+.2f}", subset=["至今%"], na_rep="—").map(style_sign, subset=["至今%"]),
            hide_index=True, width="stretch")
        st.caption("買進價是還原權值的開盤價，除權息後會和券商看到的價格不同，但報酬是對的。")

with tab_done:
    if done.empty:
        st.info("還沒有完成的交易。")
    else:
        d = done.sort_values("sell_date", ascending=False)
        st.dataframe(pd.DataFrame({
            "代號": d["stock_id"], "名稱": d["stock_id"].map(names), "評分日": d["signal_date"],
            "買進日": d["buy_date"], "賣出日": d["sell_date"], "報酬%": d["ret"],
        }).style.format("{:+.2f}", subset=["報酬%"], na_rep="—").map(style_sign, subset=["報酬%"]),
            hide_index=True, width="stretch", height=min(42 + 35 * len(d), 600))
        st.caption("買在評分隔天開盤、賣在第 10 個交易日開盤，報酬已扣交易成本。")

with tab_rules:
    st.markdown(RULES_MD)
    st.markdown("**回測結果**")
    st.markdown(BACKTEST_MD)

with tab_more:
    st.markdown("想看完整排行榜、各項分數、動能選股清單或葛蘭碧的買賣預告，可以到原本的頁面：")
    c1, c2, c3 = st.columns(3)
    with c1.container(border=True):
        st.markdown("**🎯 每日選股訊號**")
        st.caption("四面向評分排行榜、個股分數走勢、規則回測")
        if st.button("前往", key="more_signals", width="stretch"):
            st.switch_page("pages/7_每日選股訊號.py")
    with c2.container(border=True):
        st.markdown("**🚀 動能選股**")
        st.caption("趨勢模板、52 週新高突破、趨勢中回檔再起（在每日選股訊號頁的分頁）")
        if st.button("前往", key="more_momentum", width="stretch"):
            st.switch_page("pages/7_每日選股訊號.py")
    with c3.container(border=True):
        st.markdown("**📐 葛蘭碧八大法則**")
        st.caption("法則 1、2 買進預告、賣出警示、事後追蹤")
        if st.button("前往", key="more_granville", width="stretch"):
            st.switch_page("pages/6_葛蘭碧八大法則.py")

st.markdown("---")
st.caption("⚠️ 本系統資料僅供參考，不構成投資建議。投資有風險，請謹慎評估。")
