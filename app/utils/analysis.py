from datetime import date, timedelta

import pandas as pd
import streamlit as st

from utils.granville import (
    RULES, HOLD_HINT, CHOP_CROSSES,
    atr, granville_signals, ma_cross_counts, next_day_triggers, rule1_watchlist, stop_price, watch_note,
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
