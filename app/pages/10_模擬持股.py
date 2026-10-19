import streamlit as st
import pandas as pd
import sys
import os
from datetime import date, timedelta

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.db import (
    get_stock_list, get_rows_for_stocks, get_price_adjustments, apply_adjustments,
    get_paper_trades, add_paper_trades, sell_paper_trade, delete_paper_trade,
)
from utils.paper import evaluate_trades, BUY_FEE, SELL_FEE, SELL_TAX
from utils.granville import STOP_LOSS_PCT, next_day_triggers, rule_frames
from utils.ui import require_login, render_metric_cards, style_sign
require_login()

st.set_page_config(page_title="模擬持股", page_icon="💼", layout="wide")
st.title("💼 模擬持股")
st.caption(
    "從「葛蘭碧八大法則」「每日選股訊號」挑股票加入，或在下方手動新增，假設買進後過一段時間再賣出，"
    "看看實際會不會賺錢。持有中的股票用最新收盤價估算損益。"
)

if st.button("🔄 強制重新整理資料"):
    st.cache_data.clear()
    get_stock_list.cache_clear()
    st.rerun()


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
def load_exit_status(stock_ids):
    """
    持有中股票的出場判斷（葛蘭碧預設參數、20 日均線）：
    今天是否出現法則 5／6／8、均線價位、乖離，以及明天收盤跌到多少會出現法則 5／6。
    """
    since = str(date.today() - timedelta(days=120))
    raw = get_rows_for_stocks("daily_prices", list(stock_ids), since=since,
                              columns="stock_id, date, close_price")
    if raw.empty:
        return pd.DataFrame(), pd.DataFrame()
    adj = apply_adjustments(raw, get_price_adjustments(None, since))
    close = adj.assign(close_price=pd.to_numeric(adj["close_price"], errors="coerce")) \
        .pivot_table(index="date", columns="stock_id", values="close_price", aggfunc="last").sort_index()
    f = rule_frames(close)
    status = pd.DataFrame({
        "ma": f["ma"].iloc[-1], "bias": f["bias"].iloc[-1],
        **{f"r{n}": f["hits"][n].iloc[-1] for n in (5, 6, 8)},
    })
    triggers = next_day_triggers(adj, (5, 6), "賣出", list(stock_ids)).set_index("stock_id")
    return status, triggers


def exit_row(row, status, triggers):
    """一筆持股的出場狀態與說明"""
    sid, close = row["stock_id"], float(row["end_price"])
    stop = round(float(row["buy_price"]) * (1 - STOP_LOSS_PCT / 100), 2)
    st_row = status.loc[sid] if sid in status.index else None
    trig = triggers.loc[sid] if sid in triggers.index else None
    trig_price = float(trig["trigger_price"]) if trig is not None else None
    trig_note = (f"明天收盤 ≤ {trig_price:,.2f} 元（{float(trig['trigger_pct']):+.2f}%）會出現賣出訊號"
                 if trig is not None else "")
    if close <= stop:
        state = "⛔ 跌破停損"
        note = f"收盤已跌破停損價（買進價 −{STOP_LOSS_PCT:g}%），可考慮明天開盤賣出"
    elif st_row is not None and (st_row["r5"] or st_row["r6"]):
        state = "⚠️ 出現賣出訊號"
        note = "今天出現法則 " + "、".join(str(n) for n in (5, 6) if st_row[f"r{n}"]) + "（均線轉弱），可考慮明天開盤賣出"
    elif st_row is not None and st_row["r8"]:
        state = "🔥 過熱"
        note = (f"離均線 {st_row['bias']:+.1f}%。回測多半續漲，不必急著賣；"
                f"可把停損上移到均線（約 {st_row['ma']:,.2f} 元）鎖住獲利")
    elif trig is not None and float(trig["trigger_pct"]) >= -3:
        state = "🔔 接近賣出"
        note = trig_note
    else:
        state = "✅ 續抱"
        note = trig_note or "均線仍往上，明天跌停也不會出現賣出訊號"
    return {"狀態": state, "停損價": stop, "賣出觸發價": trig_price, "說明": note}


def close_on(stock_id, day):
    """某檔股票在 day（含）之前最後一個交易日的收盤價與日期"""
    rows = get_rows_for_stocks("daily_prices", [stock_id], since=str(day - timedelta(days=14)),
                               columns="stock_id, date, close_price")
    rows = rows[rows["date"] <= str(day)] if not rows.empty else rows
    if rows.empty:
        return None, None
    last = rows.iloc[-1]
    return float(last["close_price"]), last["date"]


stock_list = get_stock_list()
names = stock_list.set_index("stock_id")["stock_name"].to_dict()
trades = get_paper_trades()

if not trades.empty:
    ids = tuple(sorted(trades["stock_id"].unique()))
    since = str(pd.to_datetime(trades["buy_date"].min()) - timedelta(days=14))[:10]
    raw, adj = load_prices(ids, since)
    result = evaluate_trades(trades, raw, adj)
    result["stock_name"] = result["stock_id"].map(names).fillna("")
    result["qty"] = (pd.to_numeric(result["shares"]) * 1000).round()  # 資料表存張數，畫面用股數
    sold_mask = result["sell_date"].notna()
    holding, closed = result[~sold_mask], result[sold_mask]

    # ===== 摘要 =====
    unreal = holding["pnl"].sum()
    real = closed["pnl"].sum()
    win = (closed["pnl"] > 0).mean() * 100 if len(closed) else None
    render_metric_cards([
        ("持有中", f"{len(holding)} 筆", f"成本 {holding['cost'].sum():,.0f} 元", 0),
        ("未實現損益", f"{unreal:+,.0f}", f"{unreal / holding['cost'].sum() * 100:+.2f}%" if holding['cost'].sum() else "—",
         1 if unreal > 0 else -1 if unreal < 0 else 0),
        ("已實現損益", f"{real:+,.0f}", f"{len(closed)} 筆已賣出", 1 if real > 0 else -1 if real < 0 else 0),
        ("已賣出勝率", f"{win:.0f}%" if win is not None else "—", "賺錢筆數 ÷ 已賣出筆數", 0),
    ])

    def show(df, cols, height_rows=None):
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

    # ===== 持有中 =====
    st.subheader("📈 持有中")
    if holding.empty:
        st.info("目前沒有持有中的模擬持股")
    else:
        show(holding, {
            "stock_id": "代號", "stock_name": "名稱", "source": "來源", "buy_date": "買進日",
            "buy_price": "買進價", "qty": "股數", "end_date": "最新日期", "end_price": "最新收盤",
            "hold_days": "持有天數", "cost": "成本(元)", "pnl": "損益(元)", "pnl_pct": "報酬%", "note": "備註",
        })

        st.markdown("**🚦 出場狀態**")
        st.caption(f"停損價是買進價往下 {STOP_LOSS_PCT:g}%；賣出觸發價是明天收盤跌到多少會出現葛蘭碧法則 5／6"
                   "（20 日均線轉弱）。依最新收盤計算，僅供參考。")
        status, exit_triggers = load_exit_status(tuple(sorted(holding["stock_id"].unique())))
        exits = pd.DataFrame([{"代號": r["stock_id"], "名稱": r["stock_name"], "買進日": r["buy_date"],
                               "買進價": r["buy_price"], "最新收盤": r["end_price"], "報酬%": r["pnl_pct"],
                               **exit_row(r, status, exit_triggers)}
                              for _, r in holding.iterrows() if pd.notna(r["end_price"])])
        if not exits.empty:
            order = {"⛔ 跌破停損": 0, "⚠️ 出現賣出訊號": 1, "🔔 接近賣出": 2, "🔥 過熱": 3, "✅ 續抱": 4}
            exits = exits.sort_values("狀態", key=lambda c: c.map(order), kind="stable")
            exits = exits[["狀態", "代號", "名稱", "買進日", "買進價", "最新收盤", "報酬%", "停損價", "賣出觸發價", "說明"]]
            st.dataframe(
                exits.style
                    .format("{:,.2f}", subset=["買進價", "最新收盤", "停損價", "賣出觸發價"], na_rep="")
                    .format("{:+.2f}", subset=["報酬%"], na_rep="")
                    .map(style_sign, subset=["報酬%"]),
                hide_index=True, width="stretch", height=min(500, 38 + 35 * len(exits)),
                column_config={"代號": st.column_config.TextColumn(pinned=True),
                               "名稱": st.column_config.TextColumn(pinned=True),
                               "說明": st.column_config.TextColumn(width="large")},
            )

        with st.container(border=True):
            st.markdown("**賣出**")
            labels = {r.id: f"{r.stock_id} {r.stock_name}｜{r.buy_date} 買 {float(r.buy_price):,.2f} × {r.qty:,.0f} 股"
                      for r in holding.itertuples()}
            c1, c2, c3 = st.columns([3, 1, 1])
            pick = c1.selectbox("要賣出的持股", list(labels), format_func=labels.get, key="sell_pick")
            row = holding[holding["id"] == pick].iloc[0]
            bought = pd.to_datetime(row["buy_date"]).date()
            latest = pd.to_datetime(row["end_date"]).date() if pd.notna(row["end_date"]) else date.today()
            sell_day = c2.date_input("賣出日期", value=max(latest, bought), min_value=bought,
                                     max_value=date.today(), key=f"sell_day_{pick}")
            default_price, price_day = close_on(row["stock_id"], sell_day)
            sell_price = c3.number_input("賣出價", min_value=0.0, value=default_price or 0.0, step=0.05,
                                         format="%.2f", key=f"sell_price_{pick}_{sell_day}")
            if price_day and price_day != str(sell_day):
                st.caption(f"{sell_day} 沒有收盤價，預設帶入 {price_day} 的收盤價。")
            if st.button("確認賣出", type="primary", disabled=sell_price <= 0):
                sell_paper_trade(int(pick), str(sell_day), round(float(sell_price), 2))
                st.rerun()

    # ===== 已賣出 =====
    st.subheader("✅ 已賣出")
    if closed.empty:
        st.info("還沒有賣出的紀錄")
    else:
        show(closed.sort_values("sell_date", ascending=False), {
            "stock_id": "代號", "stock_name": "名稱", "source": "來源", "buy_date": "買進日",
            "buy_price": "買進價", "qty": "股數", "sell_date": "賣出日", "sell_price": "賣出價",
            "hold_days": "持有天數", "cost": "成本(元)", "pnl": "損益(元)", "pnl_pct": "報酬%", "note": "備註",
        })

    with st.expander("🗑️ 刪除紀錄（加錯時使用）"):
        all_labels = {r.id: f"{r.stock_id} {r.stock_name}｜{r.buy_date} 買"
                      + (f"｜{r.sell_date} 賣" if pd.notna(r.sell_date) else "｜持有中")
                      for r in result.itertuples()}
        to_delete = st.multiselect("選擇要刪除的紀錄", list(all_labels), format_func=all_labels.get)
        if st.button("刪除", disabled=not to_delete):
            for tid in to_delete:
                delete_paper_trade(int(tid))
            st.rerun()

    st.download_button(
        "📥 下載模擬持股 CSV",
        data=result.drop(columns=["created_at"], errors="ignore").to_csv(index=False, encoding="utf-8-sig"),
        file_name="模擬持股.csv", mime="text/csv",
    )
else:
    st.info("還沒有模擬持股。可以到「葛蘭碧八大法則」「每日選股訊號」頁，在列表下方的「加入模擬持股」挑股票，或用下方手動新增。")

# ===== 手動新增 =====
st.subheader("➕ 手動新增")
with st.container(border=True):
    options = stock_list["stock_id"].tolist()
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    sid = c1.selectbox("股票", options, index=None, placeholder="輸入代號或名稱",
                       format_func=lambda s: f"{s} {names.get(s, '')}")
    buy_day = c2.date_input("買進日期", value=date.today(), max_value=date.today())
    default_buy, buy_price_day = close_on(sid, buy_day) if sid else (None, None)
    buy_price = c3.number_input("買進價", min_value=0.0, value=default_buy or 0.0, step=0.05, format="%.2f",
                                key=f"buy_price_{sid}_{buy_day}")
    qty = c4.number_input("股數", min_value=1, max_value=1_000_000, value=1000, step=100,
                          help="1 張 = 1000 股；不到 1000 股就是零股")
    note = st.text_input("備註（選填）", placeholder="例如：法則2出現、外資連買")
    if sid and buy_price_day and buy_price_day != str(buy_day):
        st.caption(f"{buy_day} 沒有收盤價，預設帶入 {buy_price_day} 的收盤價。")
    if st.button("新增", type="primary", disabled=not sid or buy_price <= 0):
        add_paper_trades([{"stock_id": sid, "source": "手動", "buy_date": str(buy_day),
                           "buy_price": round(float(buy_price), 2), "shares": int(qty) / 1000, "note": note.strip()}])
        st.rerun()

st.caption(
    f"⚠️ 損益已扣買進手續費 {BUY_FEE:.4%}、賣出手續費 {SELL_FEE:.4%} 與證交稅 {SELL_TAX:.1%}（手續費未打折）。"
    "持有期間的除權息用還原權值的比例計入（假設股息再投入）。持有中的股票是假設用最新收盤價賣出估算。"
)
