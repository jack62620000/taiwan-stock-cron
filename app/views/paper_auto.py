import altair as alt
import streamlit as st
import pandas as pd
import sys
import os
from datetime import timedelta

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.glossary import render_page_guide
from utils.db import (
    get_stock_list, get_rows_for_stocks, get_price_adjustments, apply_adjustments,
    get_paper_trades, get_market_index,
)
from utils.autotrade import AUTO_GROUP_ID, START_CASH
from utils.paper import equity_curve, evaluate_trades
from utils.picks import HOLD, SLOTS, TOP_N
from utils.ui import MUTED, UP_COLOR, render_metric_cards, style_sign

# 選股頁的「明日買進模擬」分頁（pages/1_選股.py 用 runpy 執行這個檔案；登入、頁面設定、重新整理按鈕在選股頁）
# 紀錄由 auto_paper.py 每晚照「明日買進」規則寫進 AUTO_GROUP_ID 分組，這裡只顯示、不能手動新增或賣出
render_page_guide("paper_auto")


# ===== 載入資料 =====
@st.cache_data(ttl=3600)
def load_prices(stock_ids, since):
    """持股的原始收盤價與還原權值收盤價（同一份資料、同樣順序）"""
    raw = get_rows_for_stocks("daily_prices", list(stock_ids), since=since,
                              columns="stock_id, date, close_price")
    if raw.empty:
        return raw, raw
    adj = apply_adjustments(raw, get_price_adjustments(None, since))
    return raw, adj


@st.cache_data(ttl=3600)
def load_taiex(since):
    idx = get_market_index("TAIEX", limit=400)
    if idx.empty:
        return pd.Series(dtype=float)
    close = pd.to_numeric(idx.set_index("date")["close"], errors="coerce").dropna()
    return close[close.index >= since]


def render_auto_account(result, raw, adj):
    """自動帳戶成績：總資產走勢和同期加權指數比較（都從第一筆買進前一天收盤 = 100 起算）"""
    st.subheader("🤖 自動帳戶成績")
    curve = equity_curve(result, raw, adj, START_CASH)
    if curve.empty:
        return
    first = str(result["buy_date"].min())
    taiex = load_taiex(str(pd.to_datetime(first) - timedelta(days=14))[:10])
    base = taiex[taiex.index < first]
    total = curve["equity"].iloc[-1]
    ret = (total / START_CASH - 1) * 100
    peak = curve["equity"].cummax().clip(lower=START_CASH)
    mdd = ((curve["equity"] / peak - 1) * 100).min()
    tx = (taiex.iloc[-1] / base.iloc[-1] - 1) * 100 if len(base) and len(taiex) else None
    diff = ret - tx if tx is not None else None
    render_metric_cards([
        ("總資產", f"{total:,.0f}", f"起始 {START_CASH:,} 元", 0),
        ("累計報酬", f"{ret:+.2f}%", f"{first} 起", 1 if ret > 0 else -1 if ret < 0 else 0),
        ("同期加權指數", f"{tx:+.2f}%" if tx is not None else "—",
         f"贏大盤 {diff:+.2f} 個百分點" if diff is not None else None,
         (1 if diff > 0 else -1 if diff < 0 else 0) if diff is not None else 0),
        ("最大回撤", f"{mdd:.2f}%", "總資產從高點最多掉多少", -1 if mdd < 0 else 0),
    ])
    if len(base):   # 起點：第一筆買進前一天收盤，兩條線都是 100
        curve = pd.concat([pd.DataFrame({"date": [base.index[-1]], "equity": [START_CASH]}), curve], ignore_index=True)
    lines = [pd.DataFrame({"date": curve["date"], "線": "自動帳戶", "值": curve["equity"] / START_CASH * 100})]
    if len(base):
        tx_line = taiex.reindex(curve["date"]).ffill() / base.iloc[-1] * 100
        lines.append(pd.DataFrame({"date": curve["date"], "線": "加權指數", "值": tx_line.values}))
    chart = pd.concat(lines, ignore_index=True).dropna()
    st.altair_chart(alt.Chart(chart).mark_line(point=alt.OverlayMarkDef(size=15)).encode(
        x=alt.X("date:T", title=None, axis=alt.Axis(format="%m/%d")),
        y=alt.Y("值:Q", title="起點 = 100", scale=alt.Scale(zero=False)),
        color=alt.Color("線:N", scale=alt.Scale(domain=["自動帳戶", "加權指數"], range=[UP_COLOR, MUTED]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("date:T", title="日期"), "線:N", alt.Tooltip("值:Q", title="指數化", format=".1f")],
    ), width="stretch")
    st.caption("兩條線都從第一筆買進前一天收盤 = 100 開始。總資產 = 現金 + 持股用收盤價「如果今天賣出」的價值（已扣賣出手續費和證交稅）。")


def show(df, cols):
    table = df[list(cols)].rename(columns=cols)
    money = [c for c in ["損益(元)", "成本(元)"] if c in table.columns]
    prices = [c for c in ["買進價", "最新收盤", "賣出價"] if c in table.columns]
    st.dataframe(
        table.style
            .format("{:,.2f}", subset=prices, na_rep="")
            .format("{:,.0f}", subset=money, na_rep="")
            .format("{:+.2f}", subset=["報酬%"], na_rep="")
            .format("{:,.0f}", subset=["股數", "持有天數"], na_rep="")
            .map(style_sign, subset=["損益(元)", "報酬%"]),
        hide_index=True, width="stretch",
        height=min(600, 38 + 35 * len(table)),
        column_config={"代號": st.column_config.TextColumn(pinned=True),
                       "名稱": st.column_config.TextColumn(pinned=True)},
    )


st.caption(f"網站每晚照「🛒 明日買進」規則自動模擬買賣：起始資金 {START_CASH:,} 元，資金分 {SLOTS} 格；"
           f"大盤在月線和季線上時，隔天開盤買評分前 {TOP_N} 名，買進後第 {HOLD} 個交易日開盤賣出。"
           "這裡只顯示紀錄，不能手動新增或賣出。")

trades = get_paper_trades(AUTO_GROUP_ID)
if trades.empty:
    st.info("自動帳戶還沒有買賣紀錄。大盤在月線和季線上時，每晚排程跑完會照規則記錄隔天開盤的買進。")
else:
    names = get_stock_list().set_index("stock_id")["stock_name"].to_dict()
    ids = tuple(sorted(trades["stock_id"].unique()))
    since = str(pd.to_datetime(trades["buy_date"].min()) - timedelta(days=14))[:10]
    raw, adj = load_prices(ids, since)
    result = evaluate_trades(trades, raw, adj)
    result["stock_name"] = result["stock_id"].map(names).fillna("")
    result["qty"] = (pd.to_numeric(result["shares"]) * 1000).round()  # 資料表存張數，畫面用股數
    sold_mask = result["sell_date"].notna()
    holding, closed = result[~sold_mask], result[sold_mask]

    render_auto_account(result, raw, adj)

    st.subheader("📈 持有中")
    if holding.empty:
        st.info("目前沒有持有中的股票")
    else:
        show(holding, {
            "stock_id": "代號", "stock_name": "名稱", "buy_date": "買進日", "buy_price": "買進價",
            "qty": "股數", "end_date": "最新日期", "end_price": "最新收盤", "hold_days": "持有天數",
            "cost": "成本(元)", "pnl": "損益(元)", "pnl_pct": "報酬%", "note": "備註",
        })

    st.subheader("✅ 已賣出")
    if closed.empty:
        st.info("還沒有賣出的紀錄")
    else:
        win = (closed["pnl"] > 0).mean() * 100
        st.caption(f"已賣出 {len(closed)} 筆，勝率 {win:.0f}%，已實現損益 {closed['pnl'].sum():+,.0f} 元（已扣手續費和證交稅）。")
        show(closed.sort_values("sell_date", ascending=False), {
            "stock_id": "代號", "stock_name": "名稱", "buy_date": "買進日", "buy_price": "買進價",
            "qty": "股數", "sell_date": "賣出日", "sell_price": "賣出價", "hold_days": "持有天數",
            "cost": "成本(元)", "pnl": "損益(元)", "pnl_pct": "報酬%", "note": "備註",
        })

    st.download_button(
        "📥 下載明日買進模擬 CSV",
        data=result.drop(columns=["created_at"], errors="ignore").to_csv(index=False, encoding="utf-8-sig"),
        file_name="明日買進模擬.csv", mime="text/csv",
    )
