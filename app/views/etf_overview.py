import os
import sys

import pandas as pd
import streamlit as st

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.etf import CATEGORIES, PREMIUM_WARN, overview_table
from utils.etf_view import load_overview, open_card, style_premium
from utils.ui import style_sign

# ETF 頁的「ETF 總覽」分頁（pages/6_ETF.py 用 runpy 執行這個檔案；登入、頁面設定、重新整理按鈕在 ETF 頁）
data = load_overview()
dates = data["dates"]
if not dates:
    st.info("還沒有 ETF 股價資料。")
    st.stop()

table = overview_table(
    data["info"], data["latest"], data["month_ago"], data["year_ago"], data["events"], data["dividends"],
    data["stats"], data["stats_prev"], data["expense"], data["nav"], data["nav_prev"], data["ins"], dates)
st.caption(f"資料日期 {dates['latest']}，共 {len(table)} 檔有收盤價的上市櫃 ETF。")

# ===== 篩選 =====
cats = st.pills("類別", CATEGORIES, selection_mode="multi", key="etf_cats")
preset = st.pills("快速篩選", ["💰 高殖利率", "🔥 資金流入", "⚠️ 溢價過高", "🪙 低費用"], key="etf_preset")
c3, c4, c5 = st.columns([2, 2, 3])
with c3:
    min_aum = st.selectbox("規模至少", [0, 10, 50, 100, 500], format_func=lambda v: "不限" if v == 0 else f"{v} 億",
                           key="etf_min_aum")
with c4:
    min_value = st.selectbox("成交值至少", [0, 0.1, 1, 10], format_func=lambda v: "不限" if v == 0 else f"{v:g} 億",
                             key="etf_min_value")
with c5:
    keyword = st.text_input("搜尋代號或名稱", key="etf_search", placeholder="例如 0050、高股息、美債")

view = table.copy()
if cats:
    view = view[view["category"].isin(cats)]
if min_aum:
    view = view[view["規模(億)"] >= min_aum]
if min_value:
    view = view[view["成交值(億)"] >= min_value]
if keyword:
    k = keyword.strip()
    view = view[view["etf_id"].str.contains(k, case=False, regex=False)
                | view["etf_name"].fillna("").str.contains(k, case=False, regex=False)]
sort_col, note = "規模(億)", "依規模由大到小。"
if preset == "💰 高殖利率":
    view = view[view["殖利率%"].notna()]
    sort_col, note = "殖利率%", "依近 12 個月殖利率由高到低；殖利率高也可能是配息吃老本或股價跌多了，請搭配含息報酬看。"
elif preset == "🔥 資金流入":
    view = view[view["單位數增減%"] > 0]
    sort_col, note = "單位數增減%", "依發行單位數近幾天增加的比例由高到低（單位數增加代表有人申購、錢流進來）。"
elif preset == "⚠️ 溢價過高":
    view = view[view["折溢價%"].abs() >= PREMIUM_WARN]
    sort_col, note = "折溢價%", f"折溢價超過 ±{PREMIUM_WARN}% 的 ETF，市價和淨值差比較多，買在溢價時等於多付錢。"
elif preset == "🪙 低費用":
    view = view[view["年費用率%"].notna()]
    sort_col, note = "年費用率%", f"依 {dates.get('expense') or '最近一年'} 全年費用率由低到高。"
view = view.sort_values(sort_col, ascending=sort_col == "年費用率%", na_position="last").reset_index(drop=True)

# ===== 表格 =====
st.subheader(f"ETF 一覽（{len(view)} 檔）")
st.caption(note + "點一列可以開這檔的 ETF 分析卡；點欄位標題可以排序。")
show = view.rename(columns={"etf_id": "代號", "etf_name": "名稱", "category": "類別"})[
    ["代號", "名稱", "類別", "收盤", "漲跌%", "近1月%", "近1年%", "成交值(億)", "規模(億)", "受益人月增%",
     "殖利率%", "年費用率%", "折溢價%", "單位數增減%", "法人5日(張)"]]
signed = ["漲跌%", "近1月%", "近1年%", "受益人月增%", "單位數增減%", "法人5日(張)"]
event = st.dataframe(
    show.style.format("{:,.2f}", subset=["收盤", "成交值(億)", "殖利率%", "年費用率%"], na_rep="—")
        .format("{:+.2f}", subset=["漲跌%", "近1月%", "近1年%", "受益人月增%", "折溢價%", "單位數增減%"], na_rep="—")
        .format("{:,.0f}", subset=["規模(億)"], na_rep="—")
        .format("{:+,.0f}", subset=["法人5日(張)"], na_rep="—")
        .map(style_sign, subset=signed).map(style_premium, subset=["折溢價%"]),
    hide_index=True, width="stretch", height=560, on_select="rerun", selection_mode="single-row",
    key=f"etf_table_{st.session_state.get('etf_pick_n', 0)}",  # 每次開分析卡換一個 key，回來時不會還選著同一列
    column_config={"代號": st.column_config.TextColumn(pinned=True), "名稱": st.column_config.TextColumn(pinned=True)},
)
rows = event.selection.rows if event and hasattr(event, "selection") else []
if rows:
    open_card(show.iloc[rows[0]]["代號"])

ins = dates.get("ins")
st.caption(
    f"近1月%、近1年% 是含息報酬（配息再投入，用還原權值算），從 {dates.get('month') or '—'}、{dates.get('year') or '—'} 收盤起算；"
    f"殖利率 = 近 12 個月配息 ÷ 收盤。規模、受益人數是投信投顧公會月資料（{dates.get('stats') or '—'}，期貨信託類 ETF 沒有）；"
    f"年費用率是 {dates.get('expense') or '—'} 全年合計（含經理費、保管費、交易成本）。"
    f"折溢價是 {dates.get('nav') or '—'} 收盤相對預估淨值，±{PREMIUM_WARN}% 以上標橘色；"
    f"單位數增減% 是和 {dates.get('nav_prev') or '—'} 比（淨值資料從 2026-10-28 開始累積）。"
    + (f"法人5日是 {ins[0]}～{ins[1]} 三大法人合計買賣超。" if ins else "")
)
csv = view.to_csv(index=False, encoding="utf-8-sig", float_format="%.4f")
st.download_button("📥 下載 CSV", data=csv, file_name=f"ETF總覽_{dates['latest']}.csv", mime="text/csv")
