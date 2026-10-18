import streamlit as st
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from utils.ui import apply_theme
from utils.db import get_data_status
from utils.freshness import evaluate

apply_theme()


# ===== 登入驗證 =====
def check_password():
    def password_entered():
        if (st.session_state["username"] == st.secrets["USERNAME"] and
            st.session_state["password"] == st.secrets["PASSWORD"]):
            st.session_state["authenticated"] = True
        else:
            st.session_state["authenticated"] = False
            st.error("帳號或密碼錯誤")

    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if not st.session_state["authenticated"]:
        st.title("🔐 台股分析系統")
        st.text_input("帳號", key="username")
        st.text_input("密碼", type="password", key="password")
        st.button("登入", on_click=password_entered)
        return False
    return True

if not check_password():
    st.stop()
# ===== 主程式碼 =====
st.set_page_config(
    page_title="台股分析系統",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📈 台股分析系統")


# ===== 資料更新狀態 =====
@st.cache_data(ttl=3600)
def load_data_status():
    return get_data_status()

status = evaluate(load_data_status(), datetime.now(ZoneInfo("Asia/Taipei")))
stale = [r for r in status if not r["ok"]]
price_day = next((r["latest"] for r in status if r["table"] == "daily_prices"), None)
if stale:
    st.warning(f"⚠️ 有 {len(stale)} 項資料可能沒有更新："
               + "、".join(f"{r['name']}（{r['note']}）" for r in stale)
               + "。遇到國定假日會誤判，可以忽略。")
else:
    st.success(f"✅ 資料都是最新的，股價更新到 {price_day}")
with st.expander("📅 資料更新狀態"):
    st.dataframe(
        pd.DataFrame([{"資料": r["name"], "更新頻率": "每" + r["freq"], "最新資料": r["latest"] or "—",
                       "狀態": ("✅ " if r["ok"] else "⚠️ ") + r["note"]} for r in status]),
        hide_index=True, width="stretch",
    )
    st.caption("每個交易日晚上 9 點後開始抓當天資料；大戶持股每週更新，月營收每月 10 日前公布，季報依法定期限公布。")
    if st.button("🔄 強制重新整理資料"):
        st.cache_data.clear()
        st.rerun()

st.markdown("---")

st.success("### 🎯 每日選股訊號\n全市場四面向評分排行榜、個股分數走勢、規則回測")
if st.button("前往每日選股訊號", type="primary"):
    st.switch_page("pages/7_每日選股訊號.py")

st.info("### ⭐ 自選股\n一頁看完自選股的股價、評分、法人動向與營收")
if st.button("前往自選股"):
    st.switch_page("pages/8_自選股.py")

st.info("### 🔄 類股輪動\n各產業近 5 日、20 日漲幅與法人資金流向")
if st.button("前往類股輪動"):
    st.switch_page("pages/9_類股輪動.py")

col1, col2, col3, col4, col5, col6 = st.columns(6)

with col1:
    st.info("### 📊 全台股總覽\n瀏覽所有股票的多維度指標")
    if st.button("前往總覽", use_container_width=True):
        st.switch_page("pages/1_總覽.py")

with col2:
    st.info("### 🗂️ 個股分析卡\n查詢個股深度數據分析卡")
    if st.button("前往個股分析卡", use_container_width=True):
        st.switch_page("pages/2_個股分析卡.py")

with col3:
    st.info("### 🔮 期貨三大法人\n臺股期貨未平倉多日數據")
    if st.button("前往期貨三大法人", use_container_width=True):
        st.switch_page("pages/3_期貨三大法人.py")

with col4:
    st.info("### 🏦 台股外資法人\n全台股外資買賣超多日表格、連續買賣超篩選")
    if st.button("前往台股外資法人", use_container_width=True):
        st.switch_page("pages/4_台股外資法人.py")

with col5:
    st.info("### 💰 融資使用率\n全台股融資餘額與使用率多日表格")
    if st.button("前往融資使用率", use_container_width=True):
        st.switch_page("pages/5_融資融券使用率.py")

with col6:
    st.info("### 📐 葛蘭碧八大法則\n依均線與股價位置篩選買賣訊號、模擬交易")
    if st.button("前往葛蘭碧八大法則", use_container_width=True):
        st.switch_page("pages/6_葛蘭碧八大法則.py")

st.markdown("---")
st.markdown("""
### 功能說明
| 功能 | 說明 |
|------|------|
| 📊 全台股總覽 | 基本資訊、價值面、成長面、籌碼面、資金面、技術指標 |
| 🗂️ 個股分析卡 | 深度數據分析卡，含52週區間、基本面、籌碼面、技術面燈號 |
| 🔮 期貨三大法人 | 臺股期貨未平倉口數與契約金額，多日數據表格 |
| 🏦 台股外資法人 | 全台股外資買賣超多日橫向表格，支援連續買賣超篩選 |
| 💰 融資使用率 | 全台股融資餘額、融資限額與使用率多日表格，支援使用率篩選 |
| 📐 葛蘭碧八大法則 | 依 20 日（或 10 日）均線，從全台股篩出符合八大法則買進、賣出訊號的股票，並可用歷史資料模擬照法則買賣的損益 |
| 🎯 每日選股訊號 | 籌碼、資金、基本面、技術面四面向評分排行榜，個股分數走勢、AI 解讀、規則回測 |
| ⭐ 自選股 | 自選股的股價、評分與分數變化、法人買賣超、外資持股、營收，可新增、移除、寫備註 |
| 🔄 類股輪動 | 各產業近 5 日、20 日漲幅排行、輪動象限、外資與投信買賣超金額、產業內個股 |

### 資料來源
- 股價、法人、融資券：台灣證券交易所、櫃買中心
- 大戶持股：集保結算所
- 月營收、季報：公開資訊觀測站
- 期貨法人：台灣期貨交易所
""")

st.markdown("---")
st.caption("⚠️ 本系統資料僅供參考，不構成投資建議。投資有風險，請謹慎評估。")
