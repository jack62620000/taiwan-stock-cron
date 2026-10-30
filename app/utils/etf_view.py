"""
ETF 頁四個分頁共用：讀取資料（加快取）、ETF 選單、表格上色。
資料讀取都經過 utils/db.py 的 ETF 函式（ETF 專用資料庫）。
"""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from utils.db import (
    get_etf_adjustments, get_etf_dates, get_etf_dividends_between, get_etf_expense_years, get_etf_index,
    get_etf_list, get_etf_rows, get_etf_snapshot, get_etf_snapshots,
)
from utils.ui import DOWN_COLOR, UP_COLOR, WARN_COLOR

CARD_KEY = "etf_card_id"   # 要在 ETF 分析卡顯示的代號（總覽、配息行事曆點一下就切過去）


@st.cache_data(ttl=3600)
def load_etf_list():
    return get_etf_list()


def active_list(info):
    """還在交易的 ETF（沒有下市日）"""
    if info.empty:
        return info
    return info[info["delisted_date"].isna()] if "delisted_date" in info.columns else info


def etf_options(info):
    """選單用的「代號 名稱」清單（只列還在交易的）"""
    a = active_list(info)
    return (a["etf_id"] + " " + a["etf_name"].fillna("")).tolist() if not a.empty else []


def open_card(etf_id):
    """切到 ETF 分析卡顯示這檔"""
    st.session_state[CARD_KEY] = etf_id
    st.session_state["etf_pick_n"] = st.session_state.get("etf_pick_n", 0) + 1
    st.session_state["etf_goto"] = "🗂️ ETF 分析卡"
    st.rerun()


# ===== 表格上色（st.dataframe 的 Styler）=====
def style_premium(val):
    """折溢價超過 ±0.5% 標橘色"""
    from utils.etf import PREMIUM_WARN
    if val is None or pd.isna(val):
        return ""
    return f"color:{WARN_COLOR};font-weight:700;" if abs(val) >= PREMIUM_WARN else ""


def sign_color(v):
    return UP_COLOR if v > 0 else DOWN_COLOR if v < 0 else None


# ===== 總覽 =====
@st.cache_data(ttl=3600)
def load_overview():
    """總覽表要用的全部 ETF 資料：最新、約 1 個月前、約 1 年前的收盤，還原事件、配息、月資料、費用率、淨值、法人"""
    info = get_etf_list()
    today = date.today()
    days = get_etf_dates("etf_daily_prices", since=today - timedelta(days=400))
    if not days:
        return {"info": info, "dates": {}}
    latest = days[0]
    month = days[min(20, len(days) - 1)] if len(days) > 1 else None
    year_cut = (pd.Timestamp(latest) - pd.Timedelta(days=365)).strftime("%Y-%m-%d")
    year = next((d for d in days if d <= year_cut), None)
    cols = "etf_id, date, close_price, change, trade_value"
    px = get_etf_snapshots("etf_daily_prices", [d for d in (latest, month, year) if d], cols)
    pick = lambda d: px[px["date"] == d] if d and not px.empty else pd.DataFrame()  # noqa: E731
    ms = get_etf_dates("etf_monthly_stats", 2, "year_month")
    stats = get_etf_snapshot("etf_monthly_stats", "year_month", ms[0]) if ms else pd.DataFrame()
    stats_prev = get_etf_snapshot("etf_monthly_stats", "year_month", ms[1]) if len(ms) > 1 else pd.DataFrame()
    years = get_etf_expense_years()
    expense = get_etf_snapshot("etf_expense_ratio", "period", years[0]) if years else pd.DataFrame()
    nav_days = get_etf_dates("etf_nav", 6)
    nav = get_etf_snapshot("etf_nav", "date", nav_days[0]) if nav_days else pd.DataFrame()
    nav_prev = get_etf_snapshot("etf_nav", "date", nav_days[-1]) if len(nav_days) > 1 else pd.DataFrame()
    ins_days = get_etf_dates("etf_ins_daily", 5)
    ins = get_etf_snapshots("etf_ins_daily", ins_days) if ins_days else pd.DataFrame()
    return {
        "info": info, "latest": pick(latest), "month_ago": pick(month), "year_ago": pick(year),
        "events": get_etf_adjustments(None, year or month or latest),
        "dividends": get_etf_dividends_between(pd.Timestamp(latest) - pd.Timedelta(days=366), latest),
        "stats": stats, "stats_prev": stats_prev, "expense": expense, "nav": nav, "nav_prev": nav_prev, "ins": ins,
        "dates": {"latest": latest, "month": month, "year": year, "stats": ms[0] if ms else None,
                  "expense": years[0] if years else None, "nav": nav_days[0] if nav_days else None,
                  "nav_prev": nav_days[-1] if len(nav_days) > 1 else None,
                  "ins": (ins_days[-1], ins_days[0]) if ins_days else None},
    }


# ===== 分析卡 =====
@st.cache_data(ttl=3600)
def load_card(etf_id):
    """一檔 ETF 的全部資料（股價約 3 年、法人與融資借券 1 年、月資料與持股全部）"""
    today = date.today()
    year_ago = today - timedelta(days=370)
    info = get_etf_list()
    row = info[info["etf_id"] == etf_id]
    meta = {k: (None if pd.isna(v) else v) for k, v in row.iloc[0].items()} if not row.empty else {}
    prices = get_etf_rows("etf_daily_prices", etf_id)
    index_name = meta.get("index_official_name")
    index = pd.DataFrame()
    if index_name and not prices.empty:
        since = prices["date"].min()
        # 優先用報酬指數（含股利再投入，和含息報酬比才公平），沒有才用價格指數
        index = get_etf_index(index_name.removesuffix("指數") + "報酬指數", since)
        if index.empty:
            index = get_etf_index(index_name, since)
        else:
            index_name = index_name.removesuffix("指數") + "報酬指數"
    return {
        "meta": meta, "prices": prices, "events": get_etf_adjustments(etf_id),
        "dividends": get_etf_rows("etf_dividends", etf_id, "ex_date"),
        "nav": get_etf_rows("etf_nav", etf_id),
        "ins": get_etf_rows("etf_ins_daily", etf_id),   # 和股價一樣取全部，K 線選近 3 年時下方外資買賣超才完整
        "margin": get_etf_rows("etf_margin_daily", etf_id, since=year_ago),
        "sbl": get_etf_rows("etf_sbl_daily", etf_id, since=year_ago),
        "holders": get_etf_rows("etf_holder_distribution", etf_id, "week_date"),
        "stats": get_etf_rows("etf_monthly_stats", etf_id, "year_month"),
        "top": get_etf_rows("etf_top_holdings", etf_id, "year_month"),
        "expense": get_etf_rows("etf_expense_ratio", etf_id, "period"),
        "index": index, "index_name": index_name if not index.empty else None,
    }


# ===== 配息行事曆 =====
@st.cache_data(ttl=3600)
def load_calendar(past_days=30, ahead_days=90):
    """最近 past_days 天已除息、未來 ahead_days 天要除息的配息，以及這些 ETF 除息前後的收盤（算填息、殖利率）"""
    today = date.today()
    divs = get_etf_dividends_between(today - timedelta(days=past_days), today + timedelta(days=ahead_days))
    prices = pd.DataFrame()
    if not divs.empty:
        prices = get_etf_rows("etf_daily_prices", sorted(divs["etf_id"].unique()),
                              since=today - timedelta(days=past_days + 15), columns="etf_id, date, close_price")
    return divs, prices


# ===== 存股試算 =====
@st.cache_data(ttl=3600)
def load_savings(etf_ids, since):
    """存股試算：選到的 ETF 在 since 之後的原始收盤、全部配息、還原事件（用來調整分割的股數）"""
    ids = list(etf_ids)
    return (get_etf_rows("etf_daily_prices", ids, since=since, columns="etf_id, date, close_price"),
            get_etf_rows("etf_dividends", ids, "ex_date", since=since),
            get_etf_adjustments(ids))


@st.cache_data(ttl=3600)
def load_price_range():
    """ETF 股價資料的第一天和最後一天（存股試算的日期範圍）"""
    first = get_etf_rows("etf_daily_prices", "0050", columns="date")
    if first.empty:
        return None, None
    return first["date"].min(), first["date"].max()
