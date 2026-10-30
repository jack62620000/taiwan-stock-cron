import os
import runpy
import sys

import streamlit as st

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils import db
from utils.glossary import render_page_guide
from utils.ui import require_login
require_login()

st.set_page_config(page_title="ETF", page_icon="📦", layout="wide")
st.title("📦 ETF")
render_page_guide("etf")

# ETF 資料在另一個 Supabase 專案；網站的 secrets 沒設連線時只顯示提示
if db.etf_supabase is None:
    st.info("網站還沒設定 ETF 資料庫的連線（Streamlit secrets 的 ETF_SUPABASE_URL、ETF_SUPABASE_KEY），設定後這頁才有資料。")
    st.stop()

# 四個分頁各是 app/views/ 底下一個檔案；只執行選到的那一個
ETF_TABS = {
    "🔎 ETF 總覽": "etf_overview.py",
    "🗂️ ETF 分析卡": "etf_card.py",
    "📅 配息行事曆": "etf_dividends.py",
    "🧮 存股試算": "etf_savings.py",
}
# 其他分頁要切換分頁時把目標放在 etf_goto（分頁按鈕已經畫出來後不能直接改它的值）
if "etf_goto" in st.session_state:
    st.session_state["etf_tab"] = st.session_state.pop("etf_goto")
c1, c2 = st.columns([4, 1])
with c1:
    tab = st.segmented_control("分頁", list(ETF_TABS), key="etf_tab", label_visibility="collapsed") or list(ETF_TABS)[0]
with c2:
    if st.button("🔄 強制重新整理資料", width="stretch"):
        st.cache_data.clear()
        st.rerun()

runpy.run_path(os.path.join(os.path.dirname(__file__), "..", "views", ETF_TABS[tab]))
