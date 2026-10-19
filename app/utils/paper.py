import pandas as pd
import streamlit as st

# 手續費以牌價 0.1425% 計（未打折）、證交稅 0.3%（ETF 實際是 0.1%，這裡一律用 0.3% 保守估計）
BUY_FEE = 0.001425
SELL_FEE = 0.001425
SELL_TAX = 0.003
PAGE = "pages/10_模擬持股.py"


def _price_on(stock_prices, day):
    """某檔股票在 day（含）之前最後一個交易日的那一列；day 之前沒資料就取之後第一天"""
    before = stock_prices[stock_prices["date"] <= day]
    if not before.empty:
        return before.iloc[-1]
    after = stock_prices[stock_prices["date"] > day]
    return after.iloc[0] if not after.empty else None


def evaluate_trades(trades, raw, adj):
    """
    算每筆模擬交易的損益。
    raw：原始收盤價（stock_id, date, close_price），adj：同一份資料的還原權值價格。
    還沒賣出的用最新收盤價估算「如果今天賣出」；已賣出的用賣出價。
    持有期間如果除權息，用還原權值的比例把配息、配股算進去（假設股息再投入）。
    損益已扣買進手續費、賣出手續費與證交稅。
    """
    out = trades.copy()
    for col in ["end_date", "end_price", "cost", "proceeds", "pnl", "pnl_pct", "hold_days"]:
        out[col] = None
    if out.empty or raw.empty:
        for col in ["end_price", "cost", "proceeds", "pnl", "pnl_pct", "hold_days"]:
            out[col] = pd.to_numeric(out[col], errors="coerce")
        return out
    px = raw[["stock_id", "date", "close_price"]].copy()
    px["close_price"] = pd.to_numeric(px["close_price"], errors="coerce")
    px["mult"] = pd.to_numeric(adj["close_price"], errors="coerce").values / px["close_price"]
    px = px.dropna(subset=["close_price"]).sort_values(["stock_id", "date"])
    groups = {sid: g for sid, g in px.groupby("stock_id")}

    for i, t in out.iterrows():
        g = groups.get(t["stock_id"])
        if g is None or g.empty:
            continue
        buy_row = _price_on(g, str(t["buy_date"]))
        sold = pd.notna(t.get("sell_date")) and pd.notna(t.get("sell_price"))
        end_row = _price_on(g, str(t["sell_date"])) if sold else g.iloc[-1]
        end_price = float(t["sell_price"]) if sold else float(end_row["close_price"])
        # 買進到結束之間的除權息：還原比例的變化
        div_mult = float(end_row["mult"]) / float(buy_row["mult"])
        shares = float(t["shares"]) * 1000  # 資料表存的是張數（零股為小數）
        cost = float(t["buy_price"]) * shares * (1 + BUY_FEE)
        proceeds = end_price * div_mult * shares * (1 - SELL_FEE - SELL_TAX)
        end_date = str(t["sell_date"]) if sold else str(end_row["date"])
        out.at[i, "end_date"] = end_date
        out.at[i, "end_price"] = end_price
        out.at[i, "cost"] = cost
        out.at[i, "proceeds"] = proceeds
        out.at[i, "pnl"] = proceeds - cost
        out.at[i, "pnl_pct"] = (proceeds - cost) / cost * 100
        out.at[i, "hold_days"] = (pd.to_datetime(end_date) - pd.to_datetime(str(t["buy_date"]))).days
    for col in ["end_price", "cost", "proceeds", "pnl", "pnl_pct", "hold_days"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def add_form(candidates, source, buy_date, key):
    """
    在選股頁下方放「加入模擬持股」：從 candidates（stock_id, stock_name, close_price）挑股票，
    假設以 buy_date 的收盤價買進。
    """
    from utils.db import add_paper_trades
    options = candidates.dropna(subset=["close_price"]).drop_duplicates("stock_id")
    with st.expander("💼 加入模擬持股", expanded=False):
        if options.empty:
            st.info("上面沒有可以加入的股票")
            return
        st.caption(f"從上面列出的股票挑選，假設以 {buy_date} 收盤價買進；之後到「模擬持股」頁追蹤損益、決定何時賣出。")
        labels = {r.stock_id: f"{r.stock_id} {r.stock_name}（{float(r.close_price):,.2f}）"
                  for r in options.itertuples()}
        c1, c2 = st.columns([4, 1])
        picked = c1.multiselect("股票", list(labels), format_func=labels.get, key=f"paper_pick_{key}")
        qty = c2.number_input("每檔股數", min_value=1, max_value=1_000_000, value=1000, step=100, key=f"paper_qty_{key}",
                              help="1 張 = 1000 股；不到 1000 股就是零股")
        if st.button("加入模擬持股", key=f"paper_add_{key}", type="primary", disabled=not picked):
            price = options.set_index("stock_id")["close_price"]
            add_paper_trades([
                {"stock_id": sid, "source": source, "buy_date": str(buy_date),
                 "buy_price": round(float(price[sid]), 2), "shares": int(qty) / 1000, "note": ""}
                for sid in picked
            ])
            st.success(f"已加入 {len(picked)} 檔")
        try:
            st.page_link(PAGE, label="前往模擬持股頁", icon="💼")
        except Exception:  # 單獨執行這一頁（沒有從 main.py 進入）時找不到其他頁面
            st.caption("到「模擬持股」頁查看損益、決定何時賣出。")
