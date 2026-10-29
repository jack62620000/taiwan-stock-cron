import os
import runpy
import sys

import streamlit as st

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.db import get_stock_list
from utils.ui import PICKS_TAB_KEY, PICKS_TABS, require_login
require_login()

st.set_page_config(page_title="選股", page_icon="🧭", layout="wide")
st.title("🧭 選股")

# 每個分頁各是 app/views/ 底下一個檔案；只執行選到的那一個，其他分頁的資料不會載入
c1, c2 = st.columns([4, 1])
with c1:
    tab = st.segmented_control("分頁", list(PICKS_TABS), key=PICKS_TAB_KEY,
                               label_visibility="collapsed") or list(PICKS_TABS)[0]
with c2:
    if st.button("🔄 強制重新整理資料", width="stretch"):
        st.cache_data.clear()
        get_stock_list.cache_clear()
        st.rerun()

runpy.run_path(os.path.join(os.path.dirname(__file__), "..", "views", PICKS_TABS[tab]))
