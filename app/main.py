import streamlit as st


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
st.markdown("---")

st.success("### 🎯 每日選股訊號\n全市場四面向評分排行榜、個股分數走勢、規則回測")
if st.button("前往每日選股訊號", type="primary"):
    st.switch_page("pages/7_每日選股訊號.py")

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
    st.info("### 📐 葛蘭碧八大法則\n依均線與股價位置篩選買賣訊號")
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
| 📐 葛蘭碧八大法則 | 依 20 日（或 10 日）均線，從全台股篩出符合八大法則買進、賣出訊號的股票 |
| 🎯 每日選股訊號 | 籌碼、資金、基本面、技術面四面向評分排行榜，個股分數走勢、AI 解讀、規則回測 |

### 資料來源
- 股價、法人、融資券：台灣證券交易所、櫃買中心
- 大戶持股：集保結算所
- 月營收、季報：公開資訊觀測站
- 期貨法人：台灣期貨交易所
""")

st.markdown("---")
st.caption("⚠️ 本系統資料僅供參考，不構成投資建議。投資有風險，請謹慎評估。")
