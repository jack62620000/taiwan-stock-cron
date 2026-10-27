import streamlit as st
import pandas as pd
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.glossary import render_page_guide
from utils.db import (
    get_stock_list, get_signal_dates, get_top_ranks, get_market_index,
    get_rows_for_stocks, get_price_adjustments, apply_adjustments,
)
from utils.analysis import MARKET_MA, TRADE_COST_PCT, load_market_regime
from utils.picks import (BACKTEST_MD, HOLD, OPEN_AVOID_PCT, OPEN_GOOD_PCT, RULES_MD, TOP_N, buy_list,
                         market_ok_by_date, open_ref, pick_record)
from utils.paper import add_form as add_paper_form
from utils.watch import add_watch_form
from utils.ui import render_metric_cards, style_sign

# 選股頁的「明日買進」分頁（pages/0_選股.py 用 runpy 執行這個檔案；登入、頁面設定、重新整理按鈕在選股頁）
render_page_guide("picks")


# ===== 載入資料 =====
@st.cache_data(ttl=3600)
def load_today():
    """最新評分日與當天評分前 N 名（含名稱、產業、參考標籤）"""
    return buy_list()


RECORD_DAYS = 20  # 紀錄最近幾個評分日的前 N 名


@st.cache_data(ttl=3600)
def load_record():
    """最近 RECORD_DAYS 個評分日（不含最新一天，還沒買進）前 N 名，隔天開盤買、持有 HOLD 天的報酬"""
    dates = sorted(get_signal_dates(n=RECORD_DAYS + 1))
    if len(dates) < 2:
        return pd.DataFrame()
    ranks = get_top_ranks(dates[0], TOP_N)
    if ranks.empty:
        return pd.DataFrame()
    market_ok = market_ok_by_date(get_market_index("TAIEX", limit=MARKET_MA + RECORD_DAYS + 10), MARKET_MA)
    prices = get_rows_for_stocks("daily_prices", sorted(ranks["stock_id"].unique()), since=dates[0],
                                 columns="stock_id, date, open_price, close_price")
    if prices.empty:
        return pd.DataFrame()
    adj = apply_adjustments(prices, get_price_adjustments(None, dates[0]))
    return pick_record(dates, ranks, market_ok, adj, prices, HOLD, TRADE_COST_PCT)


@st.cache_data(ttl=3600)
def load_names():
    info = get_stock_list(include_delisted=True)
    return dict(zip(info["stock_id"], info["stock_name"].fillna(""))) if not info.empty else {}


last, today = load_today()
if last is None or today.empty:
    st.warning("還沒有評分資料。等今晚的每日排程跑完就會出現。")
    st.stop()
names = load_names()
regime = load_market_regime()
market_ok = bool(regime) and not regime["below"]

# ===== 大盤狀態 =====
if market_ok:
    st.success(f"🟢 加權指數 {regime['close']:,.0f} 在 {MARKET_MA} 日均線（季線）{regime['ma']:,.0f} 之上"
               f"（{regime['gap']:+.1f}%，{regime['date']}）：可以照規則買進。")
elif regime:
    st.error(f"🔴 加權指數 {regime['close']:,.0f} 在 {MARKET_MA} 日均線（季線）{regime['ma']:,.0f} 之下"
             f"（{regime['gap']:+.1f}%，{regime['date']}）：先不買新股票。")
else:
    st.warning("加權指數資料不足，無法判斷大盤是否在季線上。")

tab_buy, tab_record, tab_rules = st.tabs(["🛒 明天開盤買進", "📒 紀錄", "📖 規則與回測"])

pct = st.column_config.NumberColumn
with tab_buy:
    st.markdown(f"#### {last} 評分前 {TOP_N} 名" + ("" if market_ok else "（大盤在季線下，先不買）"))
    view = pd.DataFrame({
        "排名": today["rank"], "代號": today["stock_id"],
        "名稱": today["stock_name"], "產業": today["industry"],
        "總分": today["total_score"],
    })
    refs = [open_ref(c) for c in pd.to_numeric(today["close_price"], errors="coerce")]
    view["收盤價"] = pd.to_numeric(today["close_price"], errors="coerce")
    view["開盤較佳 ≤"] = [r[0] for r in refs]
    view["開盤不追"] = [f"{lo:,.2f}～{hi:,.2f}" if lo is not None else "" for _, lo, hi in refs]
    view["參考標籤"] = today["tags"].map("　".join)
    st.dataframe(view, hide_index=True, width="stretch",
                 column_config={"排名": pct(format="%d"), "總分": pct(format="%d", help="滿分 100（規則 v5）"),
                                "收盤價": pct(format="%.2f"),
                                "開盤較佳 ≤": pct(format="%.2f", help=f"明天開盤比今天收盤跌 {abs(OPEN_GOOD_PCT):g}% 以上，"
                                                                    "過去紀錄平均報酬最好"),
                                "開盤不追": st.column_config.TextColumn(
                                    help=f"明天開盤跳空漲 {OPEN_AVOID_PCT[0]:g}～{OPEN_AVOID_PCT[1]:g}%，過去紀錄平均報酬最差"),
                                "參考標籤": st.column_config.TextColumn(width="large")})
    st.caption(f"隔天開盤買進，持有 {HOLD} 個交易日後開盤賣出。開盤參考價、標籤都只是參考，不影響買賣"
               "（開盤參考價的根據見「📖 規則與回測」）。")
    add_watch_form(today, "選股", last, "picks_page")
    add_paper_form(today, "明日買進", last, "picks_page")

with tab_record:
    st.caption(f"最近 {RECORD_DAYS} 個評分日的前 {TOP_N} 名，如果照規則隔天開盤買、第 {HOLD} 個交易日開盤賣，"
               f"實際漲跌多少（還原權值，已扣交易成本約 {TRADE_COST_PCT:g}%）。還沒到期的用最新收盤價算。")
    try:
        rec = load_record()
    except Exception:
        rec = None
        st.warning("紀錄暫時讀取失敗，請稍後按「強制重新整理」再看一次。")
    if rec is not None and rec.empty:
        st.info("還沒有紀錄。")
    elif rec is not None:
        only_ok = st.checkbox("只看大盤在季線上的日子（規則會買的）", value=True)
        r = rec[rec["market_ok"]] if only_ok else rec
        sold = r[r["status"] == "已賣出"]["ret"].dropna()
        held = r[r["status"] != "已賣出"]["ret"].dropna()
        render_metric_cards([
            ("已賣出", f"{len(sold)} 筆", None, None),
            ("已賣出平均", f"{sold.mean():+.2f}%" if len(sold) else "—", f"勝率 {(sold > 0).mean():.0%}" if len(sold) else None,
             sold.mean() if len(sold) else None),
            ("持有中", f"{len(held)} 筆", None, None),
            ("持有中平均（至今）", f"{held.mean():+.2f}%" if len(held) else "—", None, held.mean() if len(held) else None),
        ])
        zones = []
        for z in ["較佳", "其他", "不追"]:
            zr = r[r["open_zone"] == z]
            zs = zr[zr["status"] == "已賣出"]["ret"].dropna()
            zones.append({"開盤落點": z, "筆數": len(zr), "已賣出": len(zs),
                          "已賣出平均%": zs.mean() if len(zs) else None,
                          "勝率": f"{(zs > 0).mean():.0%}" if len(zs) else "—"})
        st.markdown("**開盤落點比較**（驗證「明天開盤參考價」）")
        st.dataframe(pd.DataFrame(zones).style.format("{:+.2f}", subset=["已賣出平均%"], na_rep="—")
                     .map(style_sign, subset=["已賣出平均%"]), hide_index=True)
        st.caption(f"較佳：開盤比評分日收盤跌 {abs(OPEN_GOOD_PCT):g}% 以上；不追：跳空漲 "
                   f"{OPEN_AVOID_PCT[0]:g}～{OPEN_AVOID_PCT[1]:g}%；其他：兩者之外。筆數少時差異不代表什麼，累積越多越準。")
        table = pd.DataFrame({
            "評分日": r["signal_date"], "排名": r["rank"], "代號": r["stock_id"], "名稱": r["stock_id"].map(names),
            "大盤": r["market_ok"].map({True: "季線上", False: "季線下"}), "買進日": r["buy_date"],
            "狀態": r["status"], "買進價": r["buy_price"], "開盤漲跌%": r["open_gap"], "開盤落點": r["open_zone"],
            "賣出日": r["sell_date"], "賣出價": r["sell_price"], "最新收盤": r["last_close"], "報酬%": r["ret"],
        })
        st.dataframe(
            table.style.format("{:.0f}", subset=["排名"]).format("{:,.2f}", subset=["買進價", "賣出價", "最新收盤"], na_rep="")
                .format("{:+.2f}", subset=["報酬%", "開盤漲跌%"], na_rep="—")
                .map(style_sign, subset=["報酬%", "開盤漲跌%"]),
            hide_index=True, width="stretch", height=min(42 + 35 * len(table), 600))
        st.caption("買進價、賣出價是當天的開盤價；還沒賣出的看最新收盤。開盤漲跌% 是買進日開盤比評分日收盤漲跌多少。"
                   "報酬用還原權值計算，中間有除權息也不會失真。")

with tab_rules:
    st.markdown(RULES_MD)
    st.markdown("**回測結果**")
    st.markdown(BACKTEST_MD)
