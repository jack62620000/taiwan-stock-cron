from datetime import date, timedelta

import pandas as pd
import streamlit as st

from utils.granville import (
    RULES, HOLD_HINT, CHOP_CROSSES, WATCH_DAYS,
    atr, granville_signals, ma_cross_counts, next_day_triggers, relative_strength, rule1_watchlist, stop_price,
    watch_note,
)


@st.cache_data(ttl=3600)
def load_atr(stock_ids):
    """幾檔股票最新的 ATR（元）：抓約 45 個日曆天的還原權值股價（約 30 個交易日）"""
    from utils.db import get_rows_for_stocks, get_price_adjustments, apply_adjustments
    if not stock_ids:
        return pd.Series(dtype=float)
    since = str(date.today() - timedelta(days=45))
    raw = get_rows_for_stocks("daily_prices", list(stock_ids), since=since,
                              columns="stock_id, date, high_price, low_price, close_price")
    if raw.empty:
        return pd.Series(dtype=float)
    return atr(apply_adjustments(raw, get_price_adjustments(None, since)))


def load_watch_inputs(ids, score_days=60):
    """
    自選股分析用的資料（自選股頁與 LINE 推播共用，兩邊結果才會一致）。
    評分取最近 score_days 個評分日（看分數走勢）；股價取約 110 個日曆天（葛蘭碧要算季線），
    法人取約 70 個日曆天（法則 1 的外資條件與往回看的觀察期）。
    回傳 (sig_dates 由新到舊, signals, prices 還原權值, ins, revenue)
    """
    from utils.db import get_signal_dates, get_rows_for_stocks, get_price_adjustments, apply_adjustments
    ids = list(ids)
    sig_dates = get_signal_dates(score_days)
    signals = get_rows_for_stocks(
        "daily_signals", ids, since=min(sig_dates),
        columns="stock_id, date, total_score, rank, chip_score, margin_score, fund_score, tech_score, "
                "close_price, change_5d, change_20d, reasons, warnings",
    ) if sig_dates else pd.DataFrame()
    anchor = date.fromisoformat(sig_dates[0]) if sig_dates else date.today()
    since_prices = (anchor - timedelta(days=110)).isoformat()
    prices = get_rows_for_stocks("daily_prices", ids, since=since_prices, columns="stock_id, date, high_price, low_price, close_price, volume")
    if not prices.empty:
        prices = apply_adjustments(prices, get_price_adjustments(None, prices["date"].min()))
    ins = get_rows_for_stocks(
        "stock_ins_daily", ids, since=(anchor - timedelta(days=70)).isoformat(),
        columns="stock_id, date, foreign_net_shares, sitca_net_shares, foreign_hold_ratio",
    )
    revenue = get_rows_for_stocks(
        "monthly_revenue", ids, date_col="year_month", since=(anchor - timedelta(days=120)).strftime("%Y-%m"),
        columns="stock_id, year_month, yoy_growth",
    )
    return sig_dates, signals, prices, ins, revenue


def granville_status(ids, prices, ins, names=None):
    """
    每檔股票的葛蘭碧狀態（參數同葛蘭碧頁的預設值）：今天符合的法則、法則 1 之後的狀態、
    明天收盤到多少會出現法則 2（買進）或法則 5、6（賣出）、停損價。自選股頁與個股分析卡共用。
    prices：stock_id、date、close_price、volume（還原權值，至少約 65 個交易日才算得出季線），
    有 high_price、low_price 時用 ATR 算停損價；
    ins：stock_id、date、foreign_net_shares（法則 1 的外資條件），可以是空的。
    """
    names = names or {}
    gp = prices[["stock_id", "date", "close_price", "volume"]]
    sig = granville_signals(gp, ins=ins)
    if sig.empty:
        return pd.DataFrame()
    setups = rule1_watchlist(gp, ins=ins, min_volume=0) if ins is not None and not ins.empty else pd.DataFrame()
    setups = setups.set_index("stock_id") if not setups.empty else setups
    buy_trig = next_day_triggers(gp, (2,), "買進", stock_ids=list(ids)).set_index("stock_id")
    sell_trig = next_day_triggers(gp, (5, 6), "賣出", stock_ids=list(ids)).set_index("stock_id")
    crosses = ma_cross_counts(gp)
    atrs = atr(prices)
    lo, hi = HOLD_HINT
    rows = []
    for r in sig.itertuples():
        sid = r.stock_id
        status = setups.at[sid, "status"] if not setups.empty and sid in setups.index else None
        buy = stop = None
        sell_rules = [n for n in r.rules if n in (5, 6)]
        if sell_rules:
            state = "⚠️ 今天出現賣出訊號"
            note = f"今天出現法則{'、'.join(map(str, sell_rules))}：手上有的話，可考慮明天開盤賣出"
        elif 2 in r.rules:
            state = "✅ 明天開盤可買"
            stop = stop_price(r.close_price, atrs.get(sid))
            note = f"今天出現法則 2，回測是隔天開盤買進、持有 {lo}～{hi} 個交易日"
            if status == "今天出現法則2":
                note += "（之前也出現過法則 1）"
        elif sid in buy_trig.index:
            state = "⏳ 等待法則2"
            buy = float(buy_trig.at[sid, "trigger_price"])
            stop = stop_price(buy, atrs.get(sid))
            pct = float(buy_trig.at[sid, "trigger_pct"])
            note = (f"明天收盤 ≥ {buy:,.2f} 元（漲 {pct:.2f}% 以上）就出現法則 2" if pct > 0 else
                    f"明天收盤只要不低於 {buy:,.2f} 元就出現法則 2")
            if status == "等待法則2":
                note += "（之前已出現法則 1）"
        elif 8 in r.rules:
            state = "🔥 過熱（法則8）"
            note = f"離均線 {r.bias:+.1f}%。回測多半續漲，不必急著賣；可把停損移到均線（約 {r.ma:,.2f} 元）鎖住獲利"
        elif status is not None:
            state = "👀 法則1後觀察中" if status == "等待法則2" else f"🕒 {status}"
            note = watch_note(setups.loc[sid])
            if status == "等待法則2":
                note += "；明天還不會出現法則 2"
        elif r.rules:
            state = "📌 今天出現法則" + "、".join(map(str, r.rules))
            note = "；".join(RULES[n][1] for n in r.rules)
        else:
            state = "— 沒有訊號"
            note = f"均線{r.ma_trend}，股價在均線{'之上' if r.bias > 0 else '之下'}"
        sell = float(sell_trig.at[sid, "trigger_price"]) if sid in sell_trig.index and not sell_rules else None
        n_cross = int(crosses.get(sid, 0))
        ref = buy if buy is not None else r.close_price
        rows.append({
            "代號": sid, "名稱": names.get(sid, ""), "狀態": state,
            "今天法則": "、".join(f"法則{n}" for n in r.rules),
            "收盤價": r.close_price, "均線": r.ma, "乖離%": r.bias, "均線方向": r.ma_trend,
            "買進觸發價": buy, "停損價": stop,
            "每張風險": round((ref - stop) * 1000) if stop is not None else None,
            "賣出觸發價": sell,
            "賣出距離%": (sell / r.close_price - 1) * 100 if sell is not None else None,
            "趨勢": "🌊 盤整" if n_cross >= CHOP_CROSSES else "📈 明確",
            "說明": note,
        })
    order = {sid: k for k, sid in enumerate(ids)}
    out = pd.DataFrame(rows)
    for c in ["買進觸發價", "停損價", "每張風險", "賣出觸發價", "賣出距離%"]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out.sort_values("代號", key=lambda c: c.map(order)).reset_index(drop=True)


BUY_COLUMNS = ["狀態", "代號", "名稱", "產業", "最新收盤", "觸發價", "距離%", "停損價", "每張風險", "比大盤%",
               "趨勢", "穿越均線", "法則1日期", "說明"]


def granville_buy_table(cand, trig, ma_now, crosses, rs, atrs):
    """
    葛蘭碧頁「準備買進」表（網頁與 LINE 共用）。
    cand：rule1_watchlist 中狀態為「今天出現法則2」「等待法則2」的列（含 stock_name、industry）；
    trig：等待中股票的法則 2 觸發價（next_day_triggers，index 為 stock_id）；
    ma_now：最新均線；crosses：穿越均線次數；rs：比大盤強弱；atrs：ATR。
    回傳 (表格 DataFrame, {代號: (觸發價, 圖上標示文字)})
    """
    lo, hi = HOLD_HINT
    rows, triggers = [], {}
    for r in cand.itertuples():
        left = WATCH_DAYS - int(r.days_since)
        price = pct = None
        if r.status == "今天出現法則2":
            state, order = "✅ 明天開盤可買", 0
            note = f"今天已出現法則 2，回測建議持有 {lo}～{hi} 個交易日"
        elif r.stock_id in trig.index:
            state, order = "⏳ 等待法則2", 1
            price, pct = float(trig.at[r.stock_id, "trigger_price"]), float(trig.at[r.stock_id, "trigger_pct"])
            note = (f"明天收盤 ≥ {price:,.2f} 元（漲 {pct:.2f}% 以上）就出現法則 2" if pct > 0 else
                    f"明天收盤只要不低於 {price:,.2f} 元（跌幅在 {-pct:.2f}% 以內）就出現法則 2")
            note += f"（還剩 {left} 個交易日）"
            triggers[r.stock_id] = (price, f"收盤 ≥ {price:,.2f} 出現法則2")
        else:
            state, order = "👀 觀察中", 2
            if r.r2_c1 != "符合":
                note = "均線還沒穩定上揚，明天不會出現法則 2"
            elif r.r2_c4 != "符合":
                note = "月線還在季線之下，明天不會出現法則 2"
            elif r.close_price < ma_now.get(r.stock_id, float("nan")):
                note = f"目前在均線（約 {ma_now[r.stock_id]:,.2f} 元）之下，明天漲停也站不回，暫時不會出現法則 2"
            elif r.r2_c2 != "符合":
                note = f"還沒回檔：要先跌破均線（約 {ma_now.get(r.stock_id, float('nan')):,.2f} 元）再站回，才會出現法則 2"
            else:
                note = "法則 2 的條件還沒到齊，明天不會出現"
            note += f"（還剩 {left} 個交易日）"
        n_cross = int(crosses.get(r.stock_id, 0))
        rs_v = rs.get(r.stock_id, float("nan"))
        # 停損價以預計買進價計算：等待法則2 用觸發價，其餘用最新收盤（明天開盤價未知）
        ref = price if price is not None else float(r.close_price)
        stop = stop_price(ref, atrs.get(r.stock_id))
        rows.append({"order": order, "狀態": state, "代號": r.stock_id, "名稱": r.stock_name,
                     "比大盤%": rs_v, "停損價": stop, "每張風險": round((ref - stop) * 1000),
                     "趨勢": "🌊 盤整" if n_cross >= CHOP_CROSSES else "📈 明確", "穿越均線": n_cross,
                     "產業": r.industry, "最新收盤": r.close_price, "觸發價": price, "距離%": pct,
                     "法則1日期": r.setup_date, "說明": note})
    buy_df = pd.DataFrame(rows)
    buy_df["abs_pct"] = buy_df["距離%"].abs()
    buy_df["weak"] = ~(buy_df["比大盤%"] > 0)
    buy_df["neg_rs"] = -buy_df["比大盤%"]
    buy_df = buy_df.sort_values(["order", "weak", "abs_pct", "neg_rs", "代號"])[BUY_COLUMNS]
    return buy_df, triggers


# 葛蘭碧頁的預設參數（頁面左側「恢復預設參數」的值）
GRANVILLE_DEFAULTS = {"ma_days": 20, "slope_days": 5, "lookback": 5, "near_pct": 2.0, "bias_pct": 10.0}
GRANVILLE_MIN_VOLUME = 500


REBOUND_COLUMNS = ["代號", "名稱", "產業", "最新收盤", "均線", "乖離%", "比大盤%", "20日均量"]


def granville_rebound_table(df, rs):
    """
    急跌反彈候選：今天出現法則 4（均線往下、負乖離過大）的股票，跌越深排越前面。
    df 是 granville_signals 合併股票名稱後的表，rs 是 relative_strength。葛蘭碧頁與 LINE 共用。
    """
    hit = df[df["rules"].apply(lambda r: 4 in r).astype(bool)] if not df.empty else df
    if hit.empty:
        return pd.DataFrame(columns=REBOUND_COLUMNS)
    return pd.DataFrame({
        "代號": hit["stock_id"], "名稱": hit["stock_name"], "產業": hit["industry"],
        "最新收盤": hit["close_price"], "均線": hit["ma"], "乖離%": hit["bias"],
        "比大盤%": hit["stock_id"].map(rs), "20日均量": hit["avg_volume_20"],
    }, columns=REBOUND_COLUMNS).sort_values("乖離%").reset_index(drop=True)


def granville_default():
    """
    用葛蘭碧頁的預設參數、不加任何篩選，算出「準備買進」與「急跌反彈候選」表（LINE 查詢用，和網頁打開時看到的相同）。
    資料同葛蘭碧頁：近 100 個日曆天股價、近 60 個日曆天法人。回傳 (資料日期, 準備買進表, 急跌反彈候選表)；沒有資料時表格是空的
    """
    from utils.db import get_recent_closes, get_recent_ins, get_stock_list
    empty_buy, empty_rebound = pd.DataFrame(columns=BUY_COLUMNS), pd.DataFrame(columns=REBOUND_COLUMNS)
    prices = get_recent_closes(calendar_days=100, columns="stock_id, date, close_price, volume")
    if prices.empty:
        return None, empty_buy, empty_rebound
    latest = prices["date"].max()
    ins = get_recent_ins(calendar_days=60)
    stock_list = get_stock_list()
    signals = granville_signals(prices, ins=ins, **GRANVILLE_DEFAULTS)
    df = signals.merge(stock_list[["stock_id", "stock_name", "market", "industry"]], on="stock_id", how="inner")
    df = df[df["avg_volume_20"] >= GRANVILLE_MIN_VOLUME]
    rs = relative_strength(prices)
    rebound = granville_rebound_table(df, rs)
    watch = rule1_watchlist(prices, ins=ins, min_volume=GRANVILLE_MIN_VOLUME, **GRANVILLE_DEFAULTS)
    if watch.empty:
        return latest, empty_buy, rebound
    watch = watch[watch["stock_id"].isin(df["stock_id"])] \
        .merge(stock_list[["stock_id", "stock_name", "industry"]], on="stock_id", how="left")
    cand = watch[watch["status"].isin(["今天出現法則2", "等待法則2"])]
    if cand.empty:
        return latest, empty_buy, rebound
    waiting = sorted(cand.loc[cand["status"] == "等待法則2", "stock_id"])
    trig = next_day_triggers(prices, (2,), "買進", stock_ids=waiting, **GRANVILLE_DEFAULTS).set_index("stock_id")
    table, _ = granville_buy_table(
        cand, trig, df.set_index("stock_id")["ma"], ma_cross_counts(prices, GRANVILLE_DEFAULTS["ma_days"]),
        rs, load_atr(tuple(sorted(cand["stock_id"]))),
    )
    return latest, table, rebound

# =====================================================================
# 訊號事後追蹤：最近出現過的訊號，之後實際漲跌多少（葛蘭碧頁、每日選股頁共用）
# =====================================================================
FOLLOW_DAYS = 20  # 追蹤最近幾個交易日出現的訊號
TRADE_COST_PCT = 0.585  # 一買一賣的交易成本（%）：手續費買賣各 0.1425%＋證交稅 0.3%


def signal_followup(events, prices, indexes=None):
    """
    events：stock_id、signal_date（訊號日），其他欄位原樣保留；可以加 market 欄位（上櫃和櫃買指數比）。
    prices：這些股票訊號日之後的 stock_id、date、open_price、close_price（還原權值）。
    indexes：{"TAIEX": 加權指數, "TPEX": 櫃買指數}，欄位 date、open、close，算同期大盤用，可省略。
    同回測：訊號隔天開盤買進，算到最新收盤。還沒有隔天股價的訊號（今天剛出現）買進價留空。
    新增欄位：buy_date、buy_price、last_close、held_days（含買進當天）、ret_now、ret_max、ret_min、
    index_ret（同期大盤，%）、excess（贏大盤幾 %）。
    """
    cols = ["buy_date", "buy_price", "last_close", "held_days", "ret_now", "ret_max", "ret_min", "index_ret", "excess"]
    out = events.copy().reset_index(drop=True)
    for c in cols:
        out[c] = None
    if out.empty or prices.empty:
        return out
    by_stock = {sid: g.sort_values("date") for sid, g in prices.groupby("stock_id")}
    idx = {code: df.sort_values("date").set_index("date") for code, df in (indexes or {}).items()
           if df is not None and not df.empty}
    for i, ev in out.iterrows():
        g = by_stock.get(ev["stock_id"])
        if g is None:
            continue
        after = g[g["date"] > ev["signal_date"]]
        opens = pd.to_numeric(after["open_price"], errors="coerce")
        closes = pd.to_numeric(after["close_price"], errors="coerce")
        if after.empty or pd.isna(closes.iloc[-1]):
            continue
        buy = opens.iloc[0] if pd.notna(opens.iloc[0]) and opens.iloc[0] > 0 else closes.iloc[0]  # 沒有開盤價用收盤
        if pd.isna(buy) or buy <= 0:
            continue
        buy_date = after["date"].iloc[0]
        ret = (closes / buy - 1) * 100
        row = {"buy_date": buy_date, "buy_price": float(buy), "last_close": float(closes.iloc[-1]),
               "held_days": int(closes.notna().sum()), "ret_now": float(ret.iloc[-1]),
               "ret_max": float(ret.max()), "ret_min": float(ret.min())}
        ix = idx.get("TPEX" if ev.get("market") == "上櫃" else "TAIEX")
        if ix is not None and buy_date in ix.index:
            i_buy = pd.to_numeric(ix.at[buy_date, "open"], errors="coerce")
            i_last = pd.to_numeric(ix["close"], errors="coerce").iloc[-1]
            if pd.notna(i_buy) and i_buy > 0 and pd.notna(i_last):
                row["index_ret"] = float((i_last / i_buy - 1) * 100)
                row["excess"] = row["ret_now"] - row["index_ret"]
        for k, v in row.items():
            out.at[i, k] = v
    for c in ["buy_price", "last_close", "held_days", "ret_now", "ret_max", "ret_min", "index_ret", "excess"]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def followup_summary(df):
    """事後追蹤的整體數字：已買進幾檔、平均報酬、上漲比例、平均贏大盤；還沒有買進的訊號不算"""
    done = df[df["ret_now"].notna()]
    if done.empty:
        return None
    excess = done["excess"].dropna()
    return {"n": len(done), "pending": int(df["ret_now"].isna().sum()),
            "avg": done["ret_now"].mean(), "median": done["ret_now"].median(),
            "win": (done["ret_now"] > 0).mean(), "excess": excess.mean() if len(excess) else None}


def load_followup_prices(stock_ids, since):
    """事後追蹤用的股價：幾檔股票 since 之後的開盤、收盤（還原權值）"""
    from utils.db import apply_adjustments, get_price_adjustments, get_rows_for_stocks
    if not stock_ids:
        return pd.DataFrame(columns=["stock_id", "date", "open_price", "close_price"])
    raw = get_rows_for_stocks("daily_prices", list(stock_ids), since=since,
                              columns="stock_id, date, open_price, close_price")
    if raw.empty:
        return raw
    return apply_adjustments(raw, get_price_adjustments(None, since))


def load_followup_indexes(n=FOLLOW_DAYS + 5):
    """加權、櫃買指數最近 n 個交易日（同期大盤）"""
    from utils.db import get_market_index
    return {code: get_market_index(code, limit=n) for code in ("TAIEX", "TPEX")}


def granville_followup(days=FOLLOW_DAYS):
    """
    葛蘭碧頁「事後追蹤」：最近 days 個交易日出現過的法則 2 買點（預設參數、20 日均量 ≥ 500 張），
    隔天開盤買進到今天的漲跌。回傳 (資料日期, 表格)
    """
    from utils.db import get_recent_closes, get_recent_ins, get_stock_list
    from utils.granville import rule2_events
    # 起點最多在買點前 WATCH_DAYS 個交易日，再往前要有季線（60 日）＋均線方向的資料：約 160 個日曆天
    prices = get_recent_closes(calendar_days=160, columns="stock_id, date, close_price, volume")
    if prices.empty:
        return None, pd.DataFrame()
    ins = get_recent_ins(calendar_days=90)
    events = rule2_events(prices, ins=ins, min_volume=GRANVILLE_MIN_VOLUME, days=days,
                          watch_days=WATCH_DAYS, **GRANVILLE_DEFAULTS)
    latest = prices["date"].max()
    if events.empty:
        return latest, events
    events = events.merge(get_stock_list()[["stock_id", "stock_name", "industry", "market"]], on="stock_id",
                          how="inner")
    px = load_followup_prices(tuple(sorted(events["stock_id"].unique())), events["signal_date"].min())
    return latest, signal_followup(events, px, load_followup_indexes(days + 5))


def rebound_followup(days=FOLLOW_DAYS):
    """
    葛蘭碧頁「事後追蹤」的急跌反彈：最近 days 個交易日剛出現法則 4 的股票（預設參數、20 日均量 ≥ 500 張），
    隔天開盤買進到今天的漲跌。回傳 (資料日期, 表格)
    """
    from utils.db import get_recent_closes, get_stock_list
    from utils.granville import rule4_events
    prices = get_recent_closes(calendar_days=100, columns="stock_id, date, close_price, volume")
    if prices.empty:
        return None, pd.DataFrame()
    events = rule4_events(prices, min_volume=GRANVILLE_MIN_VOLUME, days=days, **GRANVILLE_DEFAULTS)
    latest = prices["date"].max()
    if events.empty:
        return latest, events
    events = events.merge(get_stock_list()[["stock_id", "stock_name", "industry", "market"]], on="stock_id",
                          how="inner")
    px = load_followup_prices(tuple(sorted(events["stock_id"].unique())), events["signal_date"].min())
    return latest, signal_followup(events, px, load_followup_indexes(days + 5))


MOMENTUM_FOLLOW_PICKS = ["兩者同時", "52 週新高＋量增", "剛符合趨勢模板"]


def momentum_followup(days=FOLLOW_DAYS):
    """
    每日選股頁「事後追蹤」：最近 days 個交易日出現過的動能訊號，隔天開盤買進到今天的漲跌。
    signal 欄位是這次訊號屬於哪幾類（MOMENTUM_FOLLOW_PICKS，用「、」連接）。回傳 (資料日期, 表格)
    """
    from utils.db import get_momentum_events, get_recent_dates, get_stock_list
    dates = get_recent_dates("daily_prices", n=days)
    if not dates:
        return None, pd.DataFrame()
    ev = get_momentum_events(min(dates))
    if ev.empty:
        return max(dates), ev
    both = ev["trend_template"].astype(bool) & ev["breakout"].astype(bool)
    flags = [both, ev["breakout"].astype(bool), ev["trend_new"].astype(bool)]
    ev["signal"] = ["、".join(n for n, f in zip(MOMENTUM_FOLLOW_PICKS, fs) if f) for fs in zip(*flags)]
    ev = ev.rename(columns={"date": "signal_date", "close_price": "signal_close"}) \
        .merge(get_stock_list()[["stock_id", "stock_name", "industry", "market"]], on="stock_id", how="inner") \
        .sort_values(["signal_date", "stock_id"], ascending=[False, True], ignore_index=True)
    px = load_followup_prices(tuple(sorted(ev["stock_id"].unique())), ev["signal_date"].min())
    return max(dates), signal_followup(ev, px, load_followup_indexes(days + 5))


FOLLOW_NOTE = (
    "同回測：訊號隔天開盤買進，算到最新收盤（還原權值，未扣約 0.6% 交易成本）。"
    "期間最高／最低是買進後收盤價最高、最低時的漲跌；同期大盤是同一段時間的加權指數（上櫃股票用櫃買指數）。"
    "今天剛出現的訊號明天才買進，先列在表格最上面、報酬留空。"
)


def render_followup(df, extra_cols=None):
    """事後追蹤的指標卡片與表格（葛蘭碧頁、每日選股頁共用）；extra_cols：{欄位: column_config} 放在名稱後面"""
    from utils.ui import render_metric_cards, style_sign
    s = followup_summary(df)
    if s is None:
        st.info("最近的訊號都還沒到買進日（今天剛出現），明天收盤後就有結果。")
    else:
        render_metric_cards([
            ("已買進", f"{s['n']} 檔", f"另有 {s['pending']} 檔明天才買進" if s["pending"] else "", None),
            ("平均至今漲跌", f"{s['avg']:+.2f}%", f"中位數 {s['median']:+.2f}%", s["avg"]),
            ("上漲比例", f"{s['win']:.0%}", "至今賺錢的訊號占幾成", None),
            ("平均贏大盤", f"{s['excess']:+.2f}%" if s["excess"] is not None else "—", "至今漲跌 − 同期大盤",
             s["excess"]),
        ])
    extra_cols = extra_cols or {}
    pct = ["ret_now", "ret_max", "ret_min", "index_ret", "excess"]
    cols = ["signal_date", "stock_id", "stock_name", *extra_cols, "buy_price", "last_close", "held_days", *pct]
    table = df[cols]
    st.dataframe(
        table.style.map(style_sign, subset=["ret_now", "excess"]),
        hide_index=True, width="stretch", height=min(42 + 35 * len(table), 600),
        column_config={
            "signal_date": st.column_config.TextColumn("訊號日", width="small", pinned=True),
            "stock_id": st.column_config.TextColumn("代號", width="small", pinned=True),
            "stock_name": st.column_config.TextColumn("名稱", width="small", pinned=True),
            **extra_cols,
            "buy_price": st.column_config.NumberColumn("買進價", format="%.2f", help="訊號隔天開盤價"),
            "last_close": st.column_config.NumberColumn("最新收盤", format="%.2f"),
            "held_days": st.column_config.NumberColumn("持有天數", format="%d", help="從買進當天算起的交易日數"),
            "ret_now": st.column_config.NumberColumn("至今%", format="%+.2f"),
            "ret_max": st.column_config.NumberColumn("期間最高%", format="%+.2f"),
            "ret_min": st.column_config.NumberColumn("期間最低%", format="%+.2f"),
            "index_ret": st.column_config.NumberColumn("同期大盤%", format="%+.2f"),
            "excess": st.column_config.NumberColumn("贏大盤%", format="%+.2f", help="至今% − 同期大盤%"),
        },
    )
    st.caption(FOLLOW_NOTE)


# =====================================================================
# 大盤季線提醒：加權指數收盤在 60 日均線之下時提醒（葛蘭碧頁、每日選股頁、LINE 清單共用）
# 回測（2025-02～2026-10，持有 20 日）：季線下的買進訊號平均明顯較差，所以提醒但不擋訊號
# =====================================================================
MARKET_MA = 60


def market_regime(index_df, ma_days=MARKET_MA):
    """加權指數最新收盤和 ma_days 日均線，回傳 {date, close, ma, gap, below}；資料不足回傳 None"""
    if index_df is None or index_df.empty:
        return None
    close = pd.to_numeric(index_df.sort_values("date")["close"], errors="coerce").dropna()
    if len(close) < ma_days:
        return None
    last, ma = float(close.iloc[-1]), float(close.tail(ma_days).mean())
    return {"date": str(index_df["date"].max())[:10], "close": last, "ma": ma,
            "gap": (last / ma - 1) * 100, "below": last < ma}


@st.cache_data(ttl=3600)
def load_market_regime():
    from utils.db import get_market_index
    return market_regime(get_market_index("TAIEX", limit=MARKET_MA + 5))


def market_warning(regime):
    """大盤在季線下時的提醒文字，其他情況回傳 None"""
    if not regime or not regime["below"]:
        return None
    return (f"⚠️ 大盤在季線下：加權指數 {regime['close']:,.0f}，低於 {MARKET_MA} 日均線 {regime['ma']:,.0f}"
            f"（{regime['gap']:+.1f}%，{regime['date']}）。回測顯示這段期間的買進訊號勝率較低，請降低部位或等站回季線。")


def render_market_warning():
    text = market_warning(load_market_regime())
    if text:
        st.warning(text)
