import os
import sys
from datetime import date

import pandas as pd
import streamlit as st

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.etf import CATEGORIES, fill_label, fill_status
from utils.etf_view import load_calendar, load_etf_list, open_card

# ETF 頁的「配息行事曆」分頁（pages/6_ETF.py 用 runpy 執行這個檔案；登入、頁面設定、重新整理按鈕在 ETF 頁）
PAST_DAYS, AHEAD_DAYS = 30, 90

info = load_etf_list()
divs, prices = load_calendar(PAST_DAYS, AHEAD_DAYS)
if divs.empty:
    st.info(f"最近 {PAST_DAYS} 天和未來 {AHEAD_DAYS} 天都沒有 ETF 配息資料。")
    st.stop()

meta = info.set_index("etf_id") if not info.empty else pd.DataFrame()
divs = divs.copy()
divs["名稱"] = divs["etf_id"].map(meta["etf_name"]) if not meta.empty else ""
divs["類別"] = divs["etf_id"].map(meta["category"]) if not meta.empty else ""
divs["配息頻率"] = divs["etf_id"].map(meta["dividend_frequency"]) if not meta.empty else ""
divs["cash_dividend"] = pd.to_numeric(divs["cash_dividend"], errors="coerce")
divs["pay_date"] = divs["pay_date"].fillna("—")

cats = st.pills("類別", CATEGORIES, selection_mode="multi", key="etf_div_cats")
if cats:
    divs = divs[divs["類別"].isin(cats)]

today = pd.Timestamp(date.today())
ex = pd.to_datetime(divs["ex_date"])
px = prices.copy()
if not px.empty:
    px["close_price"] = pd.to_numeric(px["close_price"], errors="coerce")
last_close = px.dropna(subset=["close_price"]).sort_values("date").groupby("etf_id")["close_price"].last() \
    if not px.empty else pd.Series(dtype=float)


def pick(table, key):
    """點一列開 ETF 分析卡"""
    ev = st.dataframe(table, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                      key=f"{key}_{st.session_state.get('etf_pick_n', 0)}",
                      column_config={"代號": st.column_config.TextColumn(pinned=True)})
    rows = ev.selection.rows if ev and hasattr(ev, "selection") else []
    if rows:
        open_card(table.data.iloc[rows[0]]["代號"] if hasattr(table, "data") else table.iloc[rows[0]]["代號"])


# ===== 即將除息 =====
up = divs[ex >= today].sort_values(["ex_date", "etf_id"])
st.subheader(f"📅 即將除息（未來 {AHEAD_DAYS} 天，{len(up)} 檔）")
if up.empty:
    st.info("未來沒有已公告的除息。")
else:
    up = up.assign(收盤=up["etf_id"].map(last_close))
    up["單次殖利率%"] = up["cash_dividend"] / up["收盤"] * 100
    up["還有幾天"] = (pd.to_datetime(up["ex_date"]) - today).dt.days
    t = up.rename(columns={"ex_date": "除息日", "etf_id": "代號", "cash_dividend": "每單位(元)", "pay_date": "發放日"})[
        ["除息日", "還有幾天", "代號", "名稱", "類別", "配息頻率", "每單位(元)", "收盤", "單次殖利率%", "發放日"]]
    pick(t.style.format("{:.3f}", subset=["每單位(元)"], na_rep="待公告")
          .format("{:,.2f}", subset=["收盤", "單次殖利率%"], na_rep="—"), "etf_div_up")
    st.caption("要領到這次配息，最晚要在除息日的前一個交易日買進並持有到收盤。單次殖利率 = 這次配息 ÷ 最新收盤；"
               "每單位配息「待公告」是投信已公告除息日、還沒公告金額。點一列可以開 ETF 分析卡。")

# ===== 最近已除息 =====
done = divs[(ex < today)].sort_values(["ex_date", "etf_id"], ascending=[False, True])
st.subheader(f"✅ 最近 {PAST_DAYS} 天已除息（{len(done)} 檔）")
if done.empty:
    st.info(f"最近 {PAST_DAYS} 天沒有 ETF 除息。")
else:
    done = fill_status(done, px if not px.empty else pd.DataFrame(columns=["etf_id", "date", "close_price"]))
    for c in ("除息前收盤", "最新收盤"):
        done[c] = pd.to_numeric(done[c], errors="coerce")
    done["殖利率%"] = done["cash_dividend"] / done["除息前收盤"] * 100
    done["填息狀態"] = [fill_label(f, n) for f, n in zip(done["填息"], done["填息天數"])]
    t = done.rename(columns={"ex_date": "除息日", "etf_id": "代號", "cash_dividend": "每單位(元)", "pay_date": "發放日"})[
        ["除息日", "代號", "名稱", "類別", "每單位(元)", "殖利率%", "除息前收盤", "最新收盤", "填息狀態", "發放日"]]
    pick(t.style.format("{:.3f}", subset=["每單位(元)"], na_rep="—")
          .format("{:,.2f}", subset=["殖利率%", "除息前收盤", "最新收盤"], na_rep="—"), "etf_div_done")
    filled = sum(1 for f in done["填息"] if f is not None and pd.notna(f) and bool(f))
    st.caption(f"{len(done)} 檔中已有 {filled} 檔填息（除息後收盤漲回除息前一天收盤）。殖利率是這次配息 ÷ 除息前一天收盤。")
