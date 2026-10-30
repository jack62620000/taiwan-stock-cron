"""
ETF 頁的計算（不碰 Streamlit 和資料庫，方便測試）：含息報酬、近 12 個月殖利率、總覽表、填息狀態、存股試算。
資料都來自 ETF 專用資料庫，欄位名稱和資料表相同（代號欄位是 etf_id）。
"""
import calendar
import math
from datetime import date

import pandas as pd

from utils.db import apply_adjustments

CATEGORIES = ["國內股票", "國外股票", "債券", "主動式", "槓桿反向", "期貨商品"]
SPLIT_EVENTS = ("分割", "反分割")
PREMIUM_WARN = 0.5        # 折溢價超過 ±0.5% 標橘色
BUY_FEE = 0.001425        # 手續費牌價 0.1425%（未打折）
MIN_FEE = 1               # 定期定額、零股多半最低 1 元


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def adjust(prices, events):
    """ETF 股價換成還原權值價格（除息、分割、反分割），算法和個股相同（db.apply_adjustments）"""
    if prices.empty or events is None or events.empty:
        return prices
    out = apply_adjustments(prices.rename(columns={"etf_id": "stock_id"}), events.rename(columns={"etf_id": "stock_id"}))
    return out.rename(columns={"stock_id": "etf_id"})


def total_return(now, then, events, then_date, now_date):
    """
    每檔 ETF 從 then_date 到 now_date 的含息報酬 %（配息再投入）：
    now、then 是 (etf_id → 收盤價) 的 Series；events 是還原權值事件，期間內每個事件的 factor 連乘後還原舊收盤價。
    """
    mult = pd.Series(1.0, index=then.index)
    if events is not None and not events.empty:
        d = pd.to_datetime(events["date"])
        ev = events[(d > pd.to_datetime(then_date)) & (d <= pd.to_datetime(now_date))]
        f = _num(ev["factor"])
        prod = f[f > 0].groupby(ev.loc[f > 0, "etf_id"]).prod()
        mult = mult.mul(prod.reindex(mult.index).fillna(1.0))
    base = _num(then) * mult
    return ((_num(now.reindex(base.index)) / base - 1) * 100).where(base > 0)


def trailing_dividends(dividends, today, days=365):
    """近 days 天（除息日在 today 之前、含今天）每檔 ETF 的配息合計（元），回傳 etf_id → 金額"""
    if dividends is None or dividends.empty:
        return pd.Series(dtype=float)
    d = pd.to_datetime(dividends["ex_date"])
    today = pd.to_datetime(today)
    recent = dividends[(d <= today) & (d > today - pd.Timedelta(days=days))]
    return _num(recent["cash_dividend"]).groupby(recent["etf_id"]).sum(min_count=1)


def overview_table(info, latest, month_ago, year_ago, events, dividends, stats, stats_prev, expense, nav, nav_prev,
                   ins, dates):
    """
    ETF 總覽表，一檔一列。dates = {"latest", "month", "year"}：三個收盤日期。
    latest／month_ago／year_ago：那天的收盤（etf_id、close_price、change、trade_value）；
    stats／stats_prev：最新和前一個月的公會月資料；expense：最近一個完整年度的費用率；
    nav／nav_prev：最新和約 5 個交易日前的淨值、單位數；ins：近 5 個交易日的三大法人。
    """
    df = info[["etf_id", "etf_name", "category", "market", "issuer"]].copy()
    if latest.empty:
        return df.iloc[0:0]
    px = latest.set_index("etf_id")
    close = _num(px["close_price"])
    df = df[df["etf_id"].isin(close.dropna().index)].copy()
    ids = df["etf_id"]
    df["收盤"] = ids.map(close)
    chg = _num(px["change"])
    prev = close - chg
    df["漲跌%"] = ids.map((chg / prev * 100).where(prev > 0))
    df["成交值(億)"] = ids.map(_num(px["trade_value"]) / 1e8)
    for col, frame, key in (("近1月%", month_ago, "month"), ("近1年%", year_ago, "year")):
        if frame is None or frame.empty or not dates.get(key):
            df[col] = float("nan")
            continue
        r = total_return(close, frame.set_index("etf_id")["close_price"], events, dates[key], dates["latest"])
        df[col] = ids.map(r)
    divs = trailing_dividends(dividends, dates["latest"])
    df["近12月配息"] = ids.map(divs)
    df["殖利率%"] = df["近12月配息"] / df["收盤"] * 100
    if not stats.empty:
        s = stats.set_index("etf_id")
        df["規模(億)"] = ids.map(_num(s["aum"]) / 1e8)
        holders = _num(s["holders_total"])
        df["受益人數"] = ids.map(holders)
        if not stats_prev.empty:
            p = _num(stats_prev.set_index("etf_id")["holders_total"])
            df["受益人月增%"] = ids.map(((holders / p.reindex(holders.index) - 1) * 100).where(p.reindex(holders.index) > 0))
    if not expense.empty:
        df["年費用率%"] = ids.map(_num(expense.set_index("etf_id")["total_pct"]))
    if not nav.empty:
        n = nav.set_index("etf_id")
        df["折溢價%"] = ids.map(_num(n["premium_pct"]))
        if not nav_prev.empty:
            units, p = _num(n["units"]), _num(nav_prev.set_index("etf_id")["units"])
            df["單位數增減%"] = ids.map(((units / p.reindex(units.index) - 1) * 100).where(p.reindex(units.index) > 0))
    if not ins.empty:
        cols = ["foreign_net_shares", "sitca_net_shares", "dealers_net_shares"]
        net = ins.assign(net=ins[cols].apply(_num).sum(axis=1, min_count=1)).groupby("etf_id")["net"].sum(min_count=1)
        df["法人5日(張)"] = ids.map(net / 1000)
    numeric = ["收盤", "漲跌%", "成交值(億)", "近1月%", "近1年%", "近12月配息", "殖利率%", "規模(億)", "受益人數",
               "受益人月增%", "年費用率%", "折溢價%", "單位數增減%", "法人5日(張)"]
    for col in numeric:
        df[col] = _num(df[col]) if col in df.columns else float("nan")   # 沒資料的格子是 NaN，表格才會顯示「—」
    return df.reset_index(drop=True)


def fill_status(dividends, prices):
    """
    已除息的配息是否填息：除息後任何一天收盤 ≥ 除息前一天收盤就算填息，回傳加上
    「除息前收盤」「最新收盤」「填息」「填息天數」欄位。prices 是這些 ETF 除息前一天起的原始收盤（etf_id、date、close_price）。
    """
    out = dividends.copy()
    if out.empty:
        return out.assign(除息前收盤=None, 最新收盤=None, 填息=None, 填息天數=None)
    px = prices.copy()
    px["date"] = pd.to_datetime(px["date"])
    px["close_price"] = _num(px["close_price"])
    px = px.dropna(subset=["close_price"]).sort_values("date")
    before, last, filled, days = [], [], [], []
    for r in out.itertuples():
        p = px[px["etf_id"] == r.etf_id]
        ex = pd.to_datetime(r.ex_date)
        pre, post = p[p["date"] < ex], p[p["date"] >= ex]
        if pre.empty or post.empty:
            before.append(None), last.append(None), filled.append(None), days.append(None)
            continue
        ref = pre["close_price"].iloc[-1]
        hit = post[post["close_price"] >= ref]
        before.append(ref)
        last.append(post["close_price"].iloc[-1])
        filled.append(not hit.empty)
        days.append(int((post["date"] <= hit["date"].iloc[0]).sum()) if not hit.empty else None)
    return out.assign(除息前收盤=before, 最新收盤=last, 填息=filled, 填息天數=days)


def fill_label(filled, days):
    """填息狀態文字（filled 是 fill_status 的「填息」欄：True／False／None）"""
    if filled is None or pd.isna(filled):
        return "—"
    if bool(filled):
        return "✅ 已填息" + (f"（第 {int(days)} 天）" if days is not None and pd.notna(days) else "")
    return "⏳ 未填息"


# ===== 存股試算 =====
def _buy_days(trading_days, start, end, day):
    """每月 day 日扣款（遇到假日或那個月沒有這天，順延到下一個交易日），回傳扣款的交易日"""
    out = []
    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        target = pd.Timestamp(date(y, m, min(day, calendar.monthrange(y, m)[1])))
        if target >= pd.Timestamp(start):
            later = trading_days[trading_days >= target]
            if len(later) and later[0] <= pd.Timestamp(end):
                out.append(later[0])
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return sorted(set(out))


def _buy(cash, price, fee_rate, min_fee):
    """用手上的現金買整數股（零股），扣手續費；回傳 (股數, 花掉的錢)"""
    if price <= 0 or cash <= 0:
        return 0, 0.0
    shares = math.floor(cash / (price * (1 + fee_rate)))
    while shares > 0:
        cost = shares * price + max(min_fee, math.floor(shares * price * fee_rate))
        if cost <= cash:
            return shares, cost
        shares -= 1
    return 0, 0.0


def xirr(flows):
    """年化報酬率（內部報酬率）：flows 是 [(日期, 金額)]，投入為負、最後市值為正；算不出來回傳 None"""
    flows = [(pd.Timestamp(d), float(v)) for d, v in flows if v]
    if len(flows) < 2 or all(v >= 0 for _, v in flows) or all(v <= 0 for _, v in flows):
        return None
    t0 = min(d for d, _ in flows)
    if (max(d for d, _ in flows) - t0).days < 1:
        return None

    def npv(r):
        return sum(v / (1 + r) ** ((d - t0).days / 365) for d, v in flows)
    lo, hi = -0.99, 10.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2 * 100


def simulate_savings(prices, dividends, events, start, end, monthly=0, day=1, lump=0, reinvest=True,
                     fee_rate=BUY_FEE, min_fee=MIN_FEE):
    """
    存股試算（一檔 ETF）。prices：原始收盤（date、close_price，不還原，照當時真的價格買）；
    dividends：etf_dividends（ex_date、pay_date、cash_dividend）；events：還原權值事件，只用分割、反分割調整股數。
    monthly：每月 day 日定期定額扣款金額；lump：start 後第一個交易日單筆投入的金額（兩者可以同時用）。
    配息：除息前一天持有的股數 × 每單位配息，在發放日（沒有發放日就用除息日）入帳；reinvest=True 時當天收盤買回同一檔。
    買股票都是整數股（零股），買不滿一股的零錢留到下次；手續費 fee_rate、最低 min_fee 元，不計賣出成本與二代健保補充保費。
    回傳 (每日明細 DataFrame, 摘要 dict)；期間沒有股價時回傳 (空表, None)。
    """
    px = prices.copy()
    px["date"] = pd.to_datetime(px["date"])
    px["close_price"] = _num(px["close_price"])
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    px = px[(px["date"] >= start) & (px["date"] <= end)].dropna(subset=["close_price"]).sort_values("date")
    if px.empty:
        return pd.DataFrame(), None
    days = pd.DatetimeIndex(px["date"])
    buys = set(_buy_days(days, start.date(), end.date(), day)) if monthly > 0 else set()
    lump_day = days[0] if lump > 0 else None

    splits = {}
    if events is not None and not events.empty:
        ev = events[events["event_type"].isin(SPLIT_EVENTS)]
        for d, f in zip(pd.to_datetime(ev["date"]), _num(ev["factor"])):
            if f and f > 0:
                splits[d] = splits.get(d, 1.0) / f   # 1 拆 4：factor 0.25，股數 × 4
    divs = {}
    if dividends is not None and not dividends.empty:
        for r in dividends.itertuples():
            cash = pd.to_numeric(r.cash_dividend, errors="coerce")
            if pd.notna(cash) and cash > 0:
                divs.setdefault(pd.Timestamp(r.ex_date), []).append(
                    (float(cash), pd.Timestamp(r.pay_date) if pd.notna(r.pay_date) else pd.Timestamp(r.ex_date)))

    units, cash, div_cash, invested, fees, spent, div_total = 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    pending = []       # (發放日, 金額)：已除息、還沒發放的配息
    flows, rows = [], []
    for d, price in zip(days, px["close_price"]):
        if d in splits:
            units = math.floor(units * splits[d] + 1e-9)
        for ex, items in list(divs.items()):
            if ex <= d:      # 除息日（遇到停止交易順延到下一個有收盤的日子），用除息前持有的股數算
                pending += [(pay, units * c) for c, pay in items]
                del divs[ex]
        paid = sum(v for pay, v in pending if pay <= d)
        pending = [(pay, v) for pay, v in pending if pay > d]
        div_total += paid
        if reinvest:
            cash += paid
        else:
            div_cash += paid   # 領現金：放著不買
        add = (monthly if d in buys else 0) + (lump if d == lump_day else 0)
        if add:
            cash += add
            invested += add
            flows.append((d, -add))
        if add or (paid and reinvest):
            n, cost = _buy(cash, price, fee_rate, min_fee)
            units += n
            cash -= cost
            spent += cost
            fees += cost - n * price
        receivable = sum(v for _, v in pending)
        rows.append({"date": d, "close_price": price, "units": units, "cash": cash + div_cash, "invested": invested,
                     "dividends": div_total, "value": units * price + cash + div_cash + receivable,
                     "receivable": receivable})
    detail = pd.DataFrame(rows)
    last = detail.iloc[-1]
    if invested <= 0:
        return detail, None
    flows.append((last["date"], last["value"]))
    summary = {
        "start": detail["date"].iloc[0].date(), "end": last["date"].date(), "invested": invested,
        "value": last["value"], "units": int(last["units"]), "dividends": div_total, "fees": fees,
        "gain": last["value"] - invested, "return_pct": (last["value"] / invested - 1) * 100,
        "xirr_pct": xirr(flows), "buys": len(buys) + (1 if lump_day is not None else 0),
        "avg_cost": spent / last["units"] if last["units"] else None,
    }
    return detail, summary


def history_growth(prices, events):
    """過去的年化股價漲幅 %（不含配息，分割已還原）：用資料庫裡最早和最新的收盤算；資料不到半年回傳 None"""
    px = prices.dropna(subset=["close_price"]).sort_values("date") if not prices.empty else prices
    if px.empty:
        return None
    split = events[events["event_type"].isin(SPLIT_EVENTS)] if events is not None and not events.empty else None
    adj = adjust(px, split) if split is not None and not split.empty else px
    first, last = _num(adj["close_price"]).iloc[0], _num(adj["close_price"]).iloc[-1]
    years = (pd.Timestamp(adj["date"].iloc[-1]) - pd.Timestamp(adj["date"].iloc[0])).days / 365.25
    if years < 0.5 or not first or first <= 0:
        return None
    return ((last / first) ** (1 / years) - 1) * 100


def project_future(prices, dividends, end, growth_pct):
    """
    存股試算往後算：最新收盤之後到 end 的每個工作日，用「每年股價漲 growth_pct %」推估收盤價；
    配息照最近 12 個月的配息月份和殖利率（每次配息 ÷ 除息前收盤）每年重複，金額跟著推估的股價變動。
    已公告、還沒除息的配息照公告金額，推估的配息從最後一筆已知配息之後開始。
    回傳 (接上推估的股價, 接上推估的配息, 最新收盤日)；推估的列 projected=True。
    """
    px = prices.copy()
    px["date"] = pd.to_datetime(px["date"])
    px["close_price"] = _num(px["close_price"])
    px = px.dropna(subset=["close_price"]).sort_values("date")
    if px.empty:
        return prices, dividends, None
    last_day, last_close = px["date"].iloc[-1], px["close_price"].iloc[-1]
    end = pd.Timestamp(end)
    px["projected"] = False
    if end <= last_day:
        return px, dividends, last_day
    days = pd.bdate_range(last_day + pd.Timedelta(days=1), end)
    g = 1 + growth_pct / 100
    price_at = lambda d: last_close * g ** ((d - last_day).days / 365.25)  # noqa: E731
    fut = pd.DataFrame({"date": days, "close_price": [round(price_at(d), 2) for d in days], "projected": True})
    out_px = pd.concat([px, fut], ignore_index=True)

    dv = dividends.copy() if dividends is not None and not dividends.empty else pd.DataFrame(
        columns=["ex_date", "pay_date", "cash_dividend"])
    dv["projected"] = False
    known = pd.to_datetime(dv["ex_date"]) if not dv.empty else pd.Series(dtype="datetime64[ns]")
    recent = dv[(known > last_day - pd.Timedelta(days=365)) & (known <= last_day)] if not dv.empty else dv
    extra = []
    after = known.max() if not dv.empty else last_day
    for r in recent.itertuples():
        ex = pd.Timestamp(r.ex_date)
        before = px[px["date"] < ex]["close_price"]
        cash = pd.to_numeric(r.cash_dividend, errors="coerce")
        if before.empty or pd.isna(cash) or cash <= 0:
            continue
        rate = cash / before.iloc[-1]
        lag = (pd.Timestamp(r.pay_date) - ex) if pd.notna(r.pay_date) else pd.Timedelta(0)
        k = 1
        while (nxt := ex + pd.DateOffset(years=k)) <= end:
            if nxt > after:
                extra.append({"ex_date": nxt.strftime("%Y-%m-%d"), "pay_date": (nxt + lag).strftime("%Y-%m-%d"),
                              "cash_dividend": round(rate * price_at(nxt), 4), "projected": True})
            k += 1
    out_dv = pd.concat([dv, pd.DataFrame(extra)], ignore_index=True) if extra else dv
    return out_px, out_dv, last_day
