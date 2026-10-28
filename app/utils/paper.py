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
    for col in ["end_date", "end_price", "div_mult", "cost", "proceeds", "pnl", "pnl_pct", "hold_days"]:
        out[col] = None
    if out.empty or raw.empty:
        for col in ["end_price", "div_mult", "cost", "proceeds", "pnl", "pnl_pct", "hold_days"]:
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
        out.at[i, "div_mult"] = div_mult
        out.at[i, "cost"] = cost
        out.at[i, "proceeds"] = proceeds
        out.at[i, "pnl"] = proceeds - cost
        out.at[i, "pnl_pct"] = (proceeds - cost) / cost * 100
        out.at[i, "hold_days"] = (pd.to_datetime(end_date) - pd.to_datetime(str(t["buy_date"]))).days
    for col in ["end_price", "div_mult", "cost", "proceeds", "pnl", "pnl_pct", "hold_days"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def add_form(candidates, source, buy_date, key):
    """
    在選股頁下方放「加入模擬持股」：從 candidates（stock_id, stock_name, close_price）挑股票，
    假設以 buy_date 的收盤價買進。
    """
    from utils.db import add_paper_trades
    from utils.groups import current_group
    gid, group_name = current_group()
    options = candidates.dropna(subset=["close_price"]).drop_duplicates("stock_id")
    with st.expander("💼 加入模擬持股", expanded=False):
        if options.empty:
            st.info("上面沒有可以加入的股票")
            return
        st.caption(f"從上面列出的股票挑選，假設以 {buy_date} 收盤價買進，放在「{group_name}」分組；"
                   "之後到「模擬持股」頁追蹤損益、決定何時賣出。")
        labels = {r.stock_id: f"{r.stock_id} {r.stock_name}（{float(r.close_price):,.2f}）"
                  for r in options.itertuples()}
        c1, c2 = st.columns([4, 1])
        picked = c1.multiselect("股票", list(labels), format_func=labels.get, key=f"paper_pick_{key}")
        qty = c2.number_input("每檔股數", min_value=1, max_value=1_000_000, value=1000, step=100, key=f"paper_qty_{key}",
                              help="1 張 = 1000 股；不到 1000 股就是零股")
        if st.button("加入模擬持股", key=f"paper_add_{key}", type="primary", disabled=not picked):
            price = options.set_index("stock_id")["close_price"]
            add_paper_trades(gid, [
                {"stock_id": sid, "source": source, "buy_date": str(buy_date),
                 "buy_price": round(float(price[sid]), 2), "shares": int(qty) / 1000, "note": ""}
                for sid in picked
            ])
            st.success(f"已加入 {len(picked)} 檔")
        try:
            st.page_link(PAGE, label="前往模擬持股頁", icon="💼")
        except Exception:  # 單獨執行這一頁（沒有從 main.py 進入）時找不到其他頁面
            st.caption("到「模擬持股」頁查看損益、決定何時賣出。")


def rule_compare(trades, raw, adj, dates, hold, top_avg):
    """
    模擬持股和「照明日買進規則」對照（檢討用）。
    trades：evaluate_trades 的結果；raw、adj：stock_id、date、open_price、close_price 的原始與還原權值價格；
    dates：交易日（由舊到新）；hold：規則持有幾個交易日；top_avg：{買進日: 同一天開盤買前 N 名、持有 hold 天的平均報酬%}。
    照規則持有：同一個買進價，在買進日之後第 hold 個交易日開盤賣（和明日買進同一天賣），還沒到期的用最新收盤。
    出場差異 = 實際報酬 − 照規則持有；選股差異 = 照規則持有 − 同天前 N 名平均。報酬都扣手續費和證交稅。
    """
    cols = ["rule_ret", "rule_status", "top_avg", "exit_diff", "pick_diff"]
    out = trades.copy()
    for c in cols:
        out[c] = None
    if out.empty or raw.empty or not dates:
        return out
    px = raw[["stock_id", "date"]].copy()
    for c in ["open_price", "close_price"]:
        px[c] = pd.to_numeric(raw[c], errors="coerce")
        px["m_" + c] = pd.to_numeric(adj[c], errors="coerce").values / px[c]
    px = px.set_index(["stock_id", "date"])
    latest = raw.assign(close_price=pd.to_numeric(raw["close_price"], errors="coerce")) \
        .dropna(subset=["close_price"]).sort_values("date").groupby("stock_id")["date"].last()
    for i, t in out.iterrows():
        sid, buy_day = t["stock_id"], str(t["buy_date"])
        k = sum(d <= buy_day for d in dates) - 1
        if k < 0 or (sid, dates[k]) not in px.index:
            continue
        buy_mult = px.at[(sid, dates[k]), "m_close_price"]
        exit_k = k + hold
        if exit_k < len(dates) and (sid, dates[exit_k]) in px.index:
            row, price_col = px.loc[(sid, dates[exit_k])], "open_price"
            out.at[i, "rule_status"] = f"{dates[exit_k]} 開盤賣"
        elif exit_k >= len(dates) and sid in latest.index:
            row, price_col = px.loc[(sid, latest[sid])], "close_price"
            out.at[i, "rule_status"] = f"持有第 {len(dates) - 1 - k} / {hold} 天"
        else:
            continue
        end, mult = row[price_col], row["m_" + price_col] / buy_mult
        if pd.isna(end) or pd.isna(mult):
            continue
        out.at[i, "rule_ret"] = (end * mult * (1 - SELL_FEE - SELL_TAX) / (float(t["buy_price"]) * (1 + BUY_FEE)) - 1) * 100
        out.at[i, "top_avg"] = top_avg.get(dates[k])
    for c in ["rule_ret", "top_avg"]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out["exit_diff"] = pd.to_numeric(out["pnl_pct"], errors="coerce") - out["rule_ret"]
    out["pick_diff"] = out["rule_ret"] - out["top_avg"]
    return out


def equity_curve(result, raw, adj, start_cash):
    """
    自動帳戶每天收盤的總資產（照規則自動模擬用）。
    result：evaluate_trades 的結果；raw、adj：stock_id、date、close_price 的原始與還原權值收盤價。
    現金 = 起始資金 − 已買進的成本 + 已賣出拿回的錢；持股市值用收盤價，除權息用還原比例算進去（和 evaluate_trades 相同）。
    回傳 DataFrame：date、equity，從第一筆買進日開始。
    """
    if result.empty or raw.empty:
        return pd.DataFrame(columns=["date", "equity"])
    px = raw[["stock_id", "date"]].copy()
    px["close"] = pd.to_numeric(raw["close_price"], errors="coerce")
    px["mult"] = pd.to_numeric(adj["close_price"], errors="coerce").values / px["close"]
    close = px.pivot_table(index="date", columns="stock_id", values="close", aggfunc="last").sort_index().ffill()
    mult = px.pivot_table(index="date", columns="stock_id", values="mult", aggfunc="last").sort_index().ffill()
    days = [d for d in close.index if d >= str(result["buy_date"].min())]
    out = []
    for d in days:
        cash, value = float(start_cash), 0.0
        for t in result.itertuples():
            buy = str(t.buy_date)
            if buy > d or pd.isna(t.cost):
                continue
            cash -= t.cost
            sold = pd.notna(t.sell_date) and str(t.sell_date) <= d
            if sold:
                cash += t.proceeds
            elif t.stock_id in close.columns and pd.notna(close.at[d, t.stock_id]):
                buy_mult = mult[t.stock_id][mult.index <= buy].dropna()
                ratio = mult.at[d, t.stock_id] / buy_mult.iloc[-1] if len(buy_mult) else 1.0
                value += close.at[d, t.stock_id] * ratio * float(t.shares) * 1000 * (1 - SELL_FEE - SELL_TAX)
        out.append({"date": d, "equity": cash + value})
    return pd.DataFrame(out)
