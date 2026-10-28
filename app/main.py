import streamlit as st
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from datetime import date, timedelta
from utils.market_trend import load_market_trend, render as render_market_trend
from utils.glossary import render_page_guide
from utils.ui import apply_theme, render_metric_cards, index_card, style_sign, go_picks_tab
from utils.db import (
    get_data_status, get_market_index, get_signal_dates, get_top_signals, get_watchlist,
    get_rows_for_stocks, get_price_adjustments, apply_adjustments, get_stock_list,
)
from utils.freshness import evaluate
from utils.groups import login, current_group, render_account_sidebar

apply_theme()


# ===== 登入驗證 =====
# 帳號在 Streamlit secrets：USERNAME / PASSWORD 是主帳號，家人的帳號在 [users.帳號]（見 utils/groups.py）
def check_password():
    def password_entered():
        if not login(st.session_state["username"], st.session_state["password"]):
            st.session_state["authenticated"] = False
            st.session_state["login_failed"] = True

    if not st.session_state.get("authenticated") or not st.session_state.get("user"):
        st.title("🔐 台股分析系統")
        st.text_input("帳號", key="username")
        st.text_input("密碼", type="password", key="password")
        st.button("登入", on_click=password_entered)
        if st.session_state.pop("login_failed", False):
            st.error("帳號或密碼錯誤")
        return False
    return True

if not check_password():
    st.stop()
render_account_sidebar()
# ===== 主程式碼 =====
st.set_page_config(
    page_title="台股分析系統",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📈 台股分析系統")
render_page_guide("home")


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
def load_home_data(watch_ids):
    """首頁用：加權與櫃買指數、最新評分日前 5 名、自選股（watch_ids，tuple）最近股價與評分"""
    taiex, tpex = get_market_index("TAIEX", 5), get_market_index("TPEX", 5)
    sig_dates = get_signal_dates(1)
    top = get_top_signals(sig_dates[0], 5) if sig_dates else pd.DataFrame()
    watch_ids = list(watch_ids)
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


taiex, tpex, sig_date, top, watch_ids, prices, signals = load_home_data(
    # 自選股清單不快取：在其他頁面加入、移除後，回首頁馬上看得到
    tuple(get_watchlist(current_group()[0])["stock_id"]))
stock_list = get_stock_list()
names = dict(zip(stock_list["stock_id"], stock_list["stock_name"].fillna(""))) if not stock_list.empty else {}

render_metric_cards([index_card("加權指數", taiex), index_card("櫃買指數", tpex)])
render_market_trend(load_market_trend())

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
    b1, b2 = st.columns(2)
    with b1:
        go_picks_tab("明天買什麼", "🛒 明日買進", "go_picks")
    with b2:
        go_picks_tab("看完整排行榜", "🏆 評分排行", "go_signals")

with watch_col:
    st.subheader(f"⭐ 自選股：{current_group()[1]}（{len(watch_ids)} 檔）")
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
    ("📊 全台股總覽", "大盤儀表板、類股輪動與全市場多維度指標", "pages/0_總覽.py"),
    ("🧭 選股", "明日買進、評分排行、均線買賣點（葛蘭碧）三個分頁", "pages/1_選股.py"),
    ("🗂️ 個股分析卡", "個股深度數據分析卡", "pages/2_個股分析卡.py"),
    ("🔮 期貨三大法人", "臺股期貨未平倉多日數據", "pages/3_期貨三大法人.py"),
    ("🏦 台股外資法人", "外資買賣超多日表格、連續買賣超篩選", "pages/4_台股外資法人.py"),
    ("💰 融資使用率", "融資餘額與使用率多日表格", "pages/5_融資融券使用率.py"),
    ("⭐ 自選股", "自選股總覽、均線買賣點與評分排行", "pages/8_自選股.py"),
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
| 🧭 選股 | 三個分頁：🛒 明日買進（大盤在月線和季線上才買評分前 10 名、持有 10 個交易日賣出；動能、布林、葛蘭碧當參考標籤，以及最近 20 個評分日照規則買賣的買進價、賣出價與漲跌紀錄）、🏆 評分排行、📐 均線買賣點 |
| 📊 全台股總覽 | 分成三個分頁：大盤（指數、成交值、漲跌家數、外資台指期，以及大盤趨勢分析：均線、量能、外資現股與台指期、支撐壓力）、類股輪動（各產業近 5 日、20 日漲幅排行、輪動象限、外資與投信買賣超金額、產業內個股）、全台股數據（基本資訊、價值面、成長面、籌碼面、資金面、技術指標） |
| 🗂️ 個股分析卡 | 深度數據分析卡（互動 K 線圖，可疊加均線、布林通道、葛蘭碧買賣點與停損價），含52週區間、基本面、籌碼面、技術面燈號，以及均線買賣點、評分排行、動能條件（趨勢模板、52 週新高）與 ATR 停損價 |
| 🔮 期貨三大法人 | 臺股期貨未平倉口數與契約金額，多日數據表格 |
| 🏦 台股外資法人 | 全台股外資買賣超多日橫向表格，支援連續買賣超篩選 |
| 💰 融資使用率 | 全台股融資餘額、融資限額與使用率多日表格，支援使用率篩選 |
| 📐 均線買賣點（選股頁分頁） | 葛蘭碧八大法則：列出出現法則 1、等待法則 2 的股票與明天的觸發價、停損價、比大盤強弱，今天出現法則 4 的急跌反彈候選，以及快出現賣出訊號的股票；進階分頁有完整八大法則篩選，可把股票加入自選股 |
| 🏆 評分排行（選股頁分頁） | 籌碼、資金、基本面、技術面四面向評分排行榜，動能選股（趨勢模板、52 週新高突破＋量增），個股分數走勢，單一個股規則回測（排名前面時之後的漲跌），可把股票加入自選股 |
| ⭐ 自選股 | 自選股的股價、法人買賣超、外資持股、營收總覽，每檔的均線買賣點狀態（觸發價、停損價、賣出警示）與評分排行、分數走勢，可加入模擬持股、新增、移除、寫備註；每個帳號可以分成多個分組並改名稱 |
| 💼 模擬持股 | 從自選股挑股票（或手動新增）假設買進，之後自己決定何時賣出，追蹤扣除手續費、證交稅後的損益，並顯示每筆持股的停損價（ATR）與賣出訊號，新增時可試算該買幾股；和自選股共用分組。「每日買進測試」分組由網站每晚照明日買進規則自動買賣、記錄買進賣出日期與價格 |

#### 資料來源
- 股價、法人、融資券：台灣證券交易所、櫃買中心
- 大戶持股：集保結算所
- 月營收、季報：公開資訊觀測站
- 期貨法人：台灣期貨交易所
""")

st.markdown("---")
st.caption("⚠️ 本系統資料僅供參考，不構成投資建議。投資有風險，請謹慎評估。")
