import streamlit as st
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from datetime import date, timedelta
from utils.ui import apply_theme, render_metric_cards, style_sign
from utils.db import (
    get_data_status, get_market_index, get_signal_dates, get_top_signals, get_watchlist,
    get_rows_for_stocks, get_price_adjustments, apply_adjustments, get_stock_list,
)
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

# ===== 今日大盤 =====
@st.cache_data(ttl=3600)
def load_home_data():
    """首頁用：加權與櫃買指數、最新評分日前 5 名、自選股最近股價與評分"""
    taiex, tpex = get_market_index("TAIEX", 5), get_market_index("TPEX", 5)
    sig_dates = get_signal_dates(1)
    top = get_top_signals(sig_dates[0], 5) if sig_dates else pd.DataFrame()
    watch_ids = get_watchlist()["stock_id"].tolist()
    prices = signals = pd.DataFrame()
    if watch_ids:
        since = (date.today() - timedelta(days=14)).isoformat()
        prices = get_rows_for_stocks("daily_prices", watch_ids, since=since, columns="stock_id, date, close_price")
        if not prices.empty:
            prices = apply_adjustments(prices, get_price_adjustments(None, prices["date"].min()))
        if sig_dates:
            signals = get_rows_for_stocks("daily_signals", watch_ids, since=sig_dates[0],
                                          columns="stock_id, date, total_score, rank")
    return taiex, tpex, (sig_dates[0] if sig_dates else None), top, watch_ids, prices, signals


def index_card(label, df):
    if df.empty:
        return (label, "—", "無資料", 0)
    last = df.iloc[-1]
    close, chg = float(last["close"]), float(last["change"])
    pct = chg / (close - chg) * 100 if close != chg else 0
    return (label, f"{close:,.2f}", f"{'▲' if chg > 0 else '▼' if chg < 0 else ''} {abs(chg):,.2f}（{pct:+.2f}%）", chg)


def watch_summary(watch_ids, prices, signals):
    """自選股：最新收盤、漲跌幅（還原權值）、今日評分與排名"""
    rows = []
    for sid in watch_ids:
        r = {"代號": sid, "名稱": names.get(sid, "")}
        p = prices[prices["stock_id"] == sid].sort_values("date") if not prices.empty else prices
        close = pd.to_numeric(p["close_price"], errors="coerce").dropna() if not p.empty else pd.Series(dtype=float)
        if not close.empty:
            r["收盤價"] = close.iloc[-1]
            r["漲跌幅%"] = (close.iloc[-1] / close.iloc[-2] - 1) * 100 if len(close) > 1 else None
        s = signals[signals["stock_id"] == sid] if not signals.empty else signals
        if not s.empty:
            r["評分"] = s.iloc[-1]["total_score"]
            r["排名"] = s.iloc[-1]["rank"]
        rows.append(r)
    df = pd.DataFrame(rows).reindex(columns=["代號", "名稱", "收盤價", "漲跌幅%", "評分", "排名"])
    for c in ["收盤價", "漲跌幅%", "評分", "排名"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


taiex, tpex, sig_date, top, watch_ids, prices, signals = load_home_data()
stock_list = get_stock_list()
names = dict(zip(stock_list["stock_id"], stock_list["stock_name"].fillna(""))) if not stock_list.empty else {}

render_metric_cards([index_card("加權指數", taiex), index_card("櫃買指數", tpex)])

top_col, watch_col = st.columns(2)
with top_col:
    st.subheader(f"🎯 今日選股前 5 名（{sig_date or '—'}）")
    if top.empty:
        st.info("還沒有評分資料。")
    else:
        table = pd.DataFrame({
            "排名": top["rank"], "代號": top["stock_id"], "名稱": top["stock_id"].map(names),
            "總分": top["total_score"], "5日%": pd.to_numeric(top["change_5d"], errors="coerce"),
            "主要理由": top["reasons"].map(lambda r: "、".join((r or [])[:2])),
        })
        st.dataframe(table.style.format("{:+.2f}", subset=["5日%"], na_rep="").map(style_sign, subset=["5日%"]),
                     hide_index=True, width="stretch")
    if st.button("看完整排行榜", key="go_signals"):
        st.switch_page("pages/7_每日選股訊號.py")

with watch_col:
    st.subheader(f"⭐ 自選股（{len(watch_ids)} 檔）")
    if not watch_ids:
        st.info("還沒有自選股，可以在自選股頁或個股分析卡加入。")
    else:
        wt = watch_summary(watch_ids, prices, signals)
        st.dataframe(
            wt.style.format("{:,.2f}", subset=["收盤價"], na_rep="")
              .format("{:+.2f}", subset=["漲跌幅%"], na_rep="")
              .format("{:,.0f}", subset=["評分", "排名"], na_rep="")
              .map(style_sign, subset=["漲跌幅%"]),
            hide_index=True, width="stretch",
        )
    if st.button("管理自選股", key="go_watch"):
        st.switch_page("pages/8_自選股.py")

# ===== 功能入口 =====
st.subheader("🧭 功能入口")
PAGES = [
    ("🎯 每日選股訊號", "四面向評分排行榜、規則回測、模擬帳戶", "pages/7_每日選股訊號.py"),
    ("⭐ 自選股", "自選股的股價、評分、法人與營收", "pages/8_自選股.py"),
    ("🔄 類股輪動", "各產業漲幅排行與法人資金流向", "pages/9_類股輪動.py"),
    ("📊 全台股總覽", "大盤儀表板與全市場多維度指標", "pages/1_總覽.py"),
    ("🗂️ 個股分析卡", "個股深度數據分析卡", "pages/2_個股分析卡.py"),
    ("🔮 期貨三大法人", "臺股期貨未平倉多日數據", "pages/3_期貨三大法人.py"),
    ("🏦 台股外資法人", "外資買賣超多日表格、連續買賣超篩選", "pages/4_台股外資法人.py"),
    ("💰 融資使用率", "融資餘額與使用率多日表格", "pages/5_融資融券使用率.py"),
    ("📐 葛蘭碧八大法則", "買進預告、賣出警示與模擬交易", "pages/6_葛蘭碧八大法則.py"),
    ("💼 模擬持股", "自己挑股票假設買進、賣出，追蹤損益", "pages/10_模擬持股.py"),
]
for start in range(0, len(PAGES), 3):
    for col, (title, desc, page) in zip(st.columns(3), PAGES[start:start + 3]):
        with col.container(border=True):
            st.markdown(f"**{title}**")
            st.caption(desc)
            if st.button("前往", key=f"nav_{page}", width="stretch"):
                st.switch_page(page)

with st.expander("ℹ️ 功能說明與資料來源"):
    st.markdown("""
#### 功能說明
| 功能 | 說明 |
|------|------|
| 📊 全台股總覽 | 基本資訊、價值面、成長面、籌碼面、資金面、技術指標 |
| 🗂️ 個股分析卡 | 深度數據分析卡，含52週區間、基本面、籌碼面、技術面燈號 |
| 🔮 期貨三大法人 | 臺股期貨未平倉口數與契約金額，多日數據表格 |
| 🏦 台股外資法人 | 全台股外資買賣超多日橫向表格，支援連續買賣超篩選 |
| 💰 融資使用率 | 全台股融資餘額、融資限額與使用率多日表格，支援使用率篩選 |
| 📐 葛蘭碧八大法則 | 列出出現法則 1、等待法則 2 的股票與明天的觸發價、停損價、比大盤強弱，以及快出現賣出訊號的股票；進階分頁有完整八大法則篩選，並可用歷史資料模擬照法則買賣的損益 |
| 🎯 每日選股訊號 | 籌碼、資金、基本面、技術面四面向評分排行榜，個股分數走勢、AI 解讀、規則回測 |
| ⭐ 自選股 | 自選股的股價、評分與分數變化、法人買賣超、外資持股、營收，可新增、移除、寫備註 |
| 💼 模擬持股 | 從葛蘭碧、每日選股挑股票（或手動新增）假設買進，之後自己決定何時賣出，追蹤扣除手續費、證交稅後的損益，並顯示每筆持股的停損價與賣出訊號 |
| 🔄 類股輪動 | 各產業近 5 日、20 日漲幅排行、輪動象限、外資與投信買賣超金額、產業內個股 |

#### 資料來源
- 股價、法人、融資券：台灣證券交易所、櫃買中心
- 大戶持股：集保結算所
- 月營收、季報：公開資訊觀測站
- 期貨法人：台灣期貨交易所
""")

st.markdown("---")
st.caption("⚠️ 本系統資料僅供參考，不構成投資建議。投資有風險，請謹慎評估。")
