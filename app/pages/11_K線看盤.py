import streamlit as st
import sys
import os
from datetime import date, timedelta

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.glossary import render_page_guide
from utils.db import get_watchlist, get_stock_list, get_rows_for_stocks, get_market_index, get_latest_date
from utils.groups import current_group
from utils.kline import build_payload, build_html
from utils.ui import require_login
require_login()

st.set_page_config(
    page_title="K線看盤",
    page_icon="🕯️",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.title("🕯️ K線看盤")
render_page_guide("kline")

if st.button("🔄 強制重新整理資料"):
    st.cache_data.clear()
    st.rerun()

# ===== 要看哪些股票 =====
group_id, group_name = current_group()
watch_ids = list(dict.fromkeys(get_watchlist(group_id)["stock_id"]))  # 自選股不快取，加入、移除後馬上看得到
stock_list = get_stock_list()
labels = {}
if not stock_list.empty:
    labels = {sid: f"{sid} {name}" for sid, name in zip(stock_list["stock_id"], stock_list["stock_name"].fillna(""))}

col1, col2 = st.columns([3, 1])
with col1:
    extra = st.multiselect("加看其他股票（不會加入自選股）", [s for s in labels if s not in watch_ids],
                           format_func=lambda s: labels.get(s, s), key="kline_extra")
with col2:
    years = st.selectbox("資料期間", [1, 2, 3], index=1, format_func=lambda y: f"近 {y} 年", key="kline_years")

stock_ids = watch_ids + [s for s in extra if s not in watch_ids]
st.caption(f"清單：⭐ {group_name}（{len(watch_ids)} 檔）" + (f"＋其他 {len(extra)} 檔" if extra else ""))


# ===== 載入資料 =====
@st.cache_data(ttl=3600)
def load_kline_data(stock_ids, years):
    """自選股的日K（原始價格，網頁裡切換還原權值）、還原權值事件、加權與櫃買指數"""
    since = (date.today() - timedelta(days=365 * years + 10)).isoformat()
    ids = list(stock_ids)
    prices = get_rows_for_stocks("daily_prices", ids, since=since,
                                 columns="stock_id, date, open_price, high_price, low_price, close_price, volume")
    # 之前的事件不影響期間內的價格，只取期間內的
    events = get_rows_for_stocks("price_adjustments", ids, since=since, columns="stock_id, date, event_type, factor")
    indexes = {code: get_market_index(code, limit=250 * years + 10) for code in ("TAIEX", "TPEX")}
    return prices, events, indexes, get_latest_date("daily_prices")


prices, events, indexes, asof = load_kline_data(tuple(stock_ids), years)
payload = build_payload(stock_ids, stock_list, prices, events, indexes, asof)

if payload is None:
    st.info("還沒有股價資料可以畫圖。可以在自選股頁或個股分析卡加入股票，或用上方「加看其他股票」挑幾檔。")
    st.stop()

if not watch_ids and not extra:
    st.info("目前自選股是空的，先顯示大盤。可以用上方「加看其他股票」挑股票，或到自選股頁加入。")

# st.components.v1.html 已被 Streamlit 標為淘汰，和個股分析卡一樣改用 st.iframe；高度跟著內容自動調整
st.iframe(build_html(payload), height="content")
st.caption("左側點股票切換；上方可切日K／週K／月K、顯示區間、原始價／還原權值、均線與布林通道；下方副圖可切 KD、MACD、RSI。"
           "滑鼠滾輪縮放、拖曳平移，手機用兩指縮放。")
