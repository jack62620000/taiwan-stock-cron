import streamlit as st
import pandas as pd
import sys
import os
from datetime import date, timedelta

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.db import (
    get_stock_list, get_signal_dates, get_rows_for_stocks, get_price_adjustments, apply_adjustments,
    get_watchlist, add_to_watchlist, update_watchlist_note, remove_from_watchlist,
)
from utils.ui import require_login, style_sign
require_login()

st.set_page_config(page_title="自選股", page_icon="⭐", layout="wide")
st.title("⭐ 自選股")
st.caption("把常看的股票加進來，一頁看完最新股價、選股評分、法人動向與營收。清單存在資料庫，換裝置也看得到。")

if st.button("🔄 強制重新整理資料"):
    st.cache_data.clear()
    get_stock_list.cache_clear()
    st.rerun()

stock_list = get_stock_list()
names = dict(zip(stock_list["stock_id"], stock_list["stock_name"].fillna(""))) if not stock_list.empty else {}
watch = get_watchlist()
watch_ids = watch["stock_id"].tolist()


# ===== 載入自選股資料 =====
@st.cache_data(ttl=3600)
def load_watch_data(ids):
    """自選股最近的評分、股價、法人與營收。ids 是 tuple，清單改變就重新載入"""
    ids = list(ids)
    sig_dates = get_signal_dates(2)
    signals = get_rows_for_stocks(
        "daily_signals", ids, since=min(sig_dates),
        columns="stock_id, date, total_score, rank, close_price, change_5d, change_20d, reasons, warnings",
    ) if sig_dates else pd.DataFrame()
    anchor = date.fromisoformat(sig_dates[0]) if sig_dates else date.today()
    since_prices = (anchor - timedelta(days=14)).isoformat()
    prices = get_rows_for_stocks("daily_prices", ids, since=since_prices, columns="stock_id, date, close_price")
    if not prices.empty:
        prices = apply_adjustments(prices, get_price_adjustments(None, prices["date"].min()))
    ins = get_rows_for_stocks(
        "stock_ins_daily", ids, since=(anchor - timedelta(days=45)).isoformat(),
        columns="stock_id, date, foreign_net_shares, sitca_net_shares, foreign_hold_ratio",
    )
    revenue = get_rows_for_stocks(
        "monthly_revenue", ids, date_col="year_month", since=(anchor - timedelta(days=120)).strftime("%Y-%m"),
        columns="stock_id, year_month, yoy_growth",
    )
    return sig_dates, signals, prices, ins, revenue


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


# ===== 自選股總表 =====
if not watch_ids:
    st.info("還沒有自選股，用下方「新增自選股」加入，或在個股分析卡按「⭐ 加入自選股」。")
else:
    sig_dates, signals, prices, ins, revenue = load_watch_data(tuple(watch_ids))
    table = build_table(watch_ids, sig_dates, signals, prices, ins, revenue)
    st.subheader(f"📋 自選股總覽（{len(watch_ids)} 檔，評分日 {sig_dates[0] if sig_dates else '—'}）")
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

    # 今日加分理由與風險提醒
    if not signals.empty:
        latest = signals[signals["date"] == signals["date"].max()]
        with st.expander("📝 今日加分理由與風險提醒"):
            for sid in watch_ids:
                row = latest[latest["stock_id"] == sid]
                if row.empty:
                    continue
                row = row.iloc[0]
                reasons = "、".join(row["reasons"] or []) or "無"
                warnings = "、".join(row["warnings"] or [])
                st.markdown(f"**{sid} {names.get(sid, '')}**：{reasons}" + (f"　⚠️ {warnings}" if warnings else ""))

    # 跳到個股分析卡
    jump_col, _ = st.columns([2, 3])
    with jump_col:
        pick = st.selectbox("查看個股分析卡", watch_ids, format_func=lambda s: f"{s} {names.get(s, '')}")
        if st.button("前往個股分析卡"):
            st.session_state["card_stock_id"] = pick
            st.switch_page("pages/2_個股分析卡.py")

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
        add_to_watchlist(new_id, new_note.strip())
        st.cache_data.clear()
        st.rerun()

with remove_col:
    st.markdown("**移除自選股**")
    to_remove = st.multiselect("選擇要移除的股票", watch_ids, format_func=lambda s: f"{s} {names.get(s, '')}")
    if st.button("🗑️ 移除", disabled=not to_remove):
        for sid in to_remove:
            remove_from_watchlist(sid)
        st.cache_data.clear()
        st.rerun()

if watch_ids:
    st.markdown("**編輯備註**")
    notes = watch.assign(名稱=watch["stock_id"].map(names),
                         加入日期=pd.to_datetime(watch["added_at"]).dt.tz_convert("Asia/Taipei").dt.strftime("%Y-%m-%d"))
    notes = notes.rename(columns={"stock_id": "代號", "note": "備註"})[["代號", "名稱", "加入日期", "備註"]]
    edited = st.data_editor(notes, hide_index=True, width="stretch",
                            disabled=["代號", "名稱", "加入日期"], key="watch_notes")
    changed = edited[edited["備註"].fillna("") != notes["備註"].fillna("")]
    if st.button("💾 儲存備註", disabled=changed.empty):
        for _, r in changed.iterrows():
            update_watchlist_note(r["代號"], (r["備註"] or "").strip())
        st.success(f"已儲存 {len(changed)} 筆備註")
        st.rerun()
