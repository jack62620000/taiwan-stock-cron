"""產生測試用的假資料（固定亂數種子，每次一樣），欄位和 Supabase 上的資料表相同"""
import numpy as np
import pandas as pd

N_DAYS = 300
# (代號, 名稱, 市場, 產業, 每日平均漲跌 %)：有長多股、空頭股、盤整股，讓各種訊號都有機會出現
STOCKS = [
    ("2330", "台積電", "上市", "半導體業", 0.25),
    ("2317", "鴻海", "上市", "其他電子業", -0.15),
    ("2454", "聯發科", "上市", "半導體業", 0.05),
    ("1101", "台泥", "上市", "水泥工業", 0.0),
    ("6488", "環球晶", "上櫃", "半導體業", 0.12),
    ("3583", "辛耘", "上市", "半導體業", 0.4),
]


def trading_days(n=N_DAYS):
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)]


def build_tables():
    rng = np.random.default_rng(42)
    days = trading_days()
    t = {k: [] for k in [
        "stock_info", "daily_prices", "price_adjustments", "stock_ins_daily", "margin_utilization",
        "holder_distribution", "monthly_revenue", "quarterly_report", "market_index",
        "futures_institutional", "daily_signals", "momentum_signals", "watch_groups", "watchlist", "paper_trades",
        "us_market_daily", *ETF_TABLES,
    ]}
    for sid, name, market, industry, drift in STOCKS:
        t["stock_info"].append({"stock_id": sid, "stock_name": name, "industry": industry, "market": market,
                                "shares_issued": 1_000_000_000, "updated_at": days[-1], "delisted_date": None})
        close = 100 * np.exp(np.cumsum(rng.normal(drift / 100, 0.018, len(days))))
        vol = rng.integers(800_000, 6_000_000, len(days))
        vol[-1] = vol[-1] * 2  # 最後一天爆量
        for i, d in enumerate(days):
            c = round(float(close[i]), 2)
            t["daily_prices"].append({
                "stock_id": sid, "date": d, "open_price": round(c * (1 + rng.normal(0, 0.005)), 2),
                "high_price": round(c * 1.015, 2), "low_price": round(c * 0.985, 2), "close_price": c,
                "volume": int(vol[i])})
            t["stock_ins_daily"].append({
                "stock_id": sid, "date": d, "foreign_net_shares": int(rng.normal(0, 300_000)),
                "sitca_net_shares": int(rng.normal(0, 50_000)), "dealers_net_shares": int(rng.normal(0, 30_000)),
                "foreign_hold_shares": 500_000_000, "foreign_hold_ratio": round(50 + i * 0.01, 2)})
        for d in days[-60:]:
            t["margin_utilization"].append({
                "stock_id": sid, "date": d, "margin_balance": int(rng.integers(1_000, 20_000)), "margin_limit": 250_000,
                "margin_rate": round(float(rng.uniform(1, 25)), 2), "short_balance": int(rng.integers(0, 2_000)),
                "short_rate": round(float(rng.uniform(0, 5)), 2)})
        for d in days[-100::5]:
            t["holder_distribution"].append({"stock_id": sid, "week_date": d,
                                             "big_holder_ratio": round(float(rng.uniform(50, 80)), 2)})
        for k, ym in enumerate(pd.period_range(end=pd.Timestamp.today(), periods=14, freq="M")[:-1]):
            t["monthly_revenue"].append({
                "stock_id": sid, "year_month": ym.strftime("%Y-%m"), "revenue": int(rng.integers(1e6, 1e8)),
                "mom_growth": round(float(rng.normal(2, 10)), 2), "yoy_growth": round(float(rng.normal(10, 20)), 2),
                "cumulative_revenue": int(1e9), "cumulative_yoy_growth": round(float(rng.normal(10, 10)), 2)})
        year = pd.Timestamp.today().year
        for k, (y, q) in enumerate([(year - 2, 3), (year - 2, 4), (year - 1, 1), (year - 1, 2),
                                    (year - 1, 3), (year - 1, 4), (year, 1), (year, 2)]):
            eps = round(q * float(rng.uniform(0.5, 3)), 2)
            t["quarterly_report"].append({
                "stock_id": sid, "year": y, "quarter": q, "eps": eps, "book_value": 50.0, "pb_ratio": 2.0,
                "roe": round(q * 3.5, 2), "revenue": 1e8 * q, "gross_margin": 40.0, "operating_margin": 20.0,
                "debt_ratio": 35.0, "current_ratio": 180.0, "net_income": 1e7 * q, "equity_total": 5e8,
                "operating_cf": int(5e6 * q), "investing_cf": int(-2e6 * q), "financing_cf": int(-1e6 * q),
                "free_cf": int(3e6 * q), "updated_at": days[-1]})
        for i, d in enumerate(days[-60:]):
            score = int(rng.integers(30, 95))
            t["daily_signals"].append({
                "stock_id": sid, "date": d, "total_score": score, "chip_score": min(32, score // 3),
                "margin_score": 4, "fund_score": min(35, score // 3), "tech_score": min(25, score // 4),
                "rank": int(rng.integers(1, 50)), "reasons": ["站上月線", "外資連買"], "warnings": [],
                "close_price": float(close[len(days) - 60 + i]), "change_5d": 1.2, "change_20d": 4.5,
                "avg_volume_20": 2_000_000, "liquid": True, "rule_version": 3})
        for j, d in enumerate(days[-5:]):
            c = float(close[len(days) - 5 + j])
            up = drift > 0.1
            t["momentum_signals"].append({
                "stock_id": sid, "date": d, "close_price": c, "ma50": c * 0.95, "ma150": c * 0.9,
                "ma200": c * (0.85 if up else 1.1), "ma200_prev": c * (0.83 if up else 1.12),
                "high_52w": c * 1.02, "low_52w": c * 0.6, "rs_rating": 90 if up else 30, "vol_ratio": 2.0,
                "atr14": c * 0.03, "trend_checks": 8 if up else 3, "trend_template": up, "trend_new": up and j == 4,
                "new_high": up, "breakout": up and j in (1, 4), "pullback": up and j == 2, "liquid": True})
    # 測試帳號 tester 的分組、自選股與模擬持股（一筆持有中、一筆已賣出）
    t["watch_groups"].append({"id": 1, "owner": "tester", "name": "我的自選股", "created_at": days[0] + "T03:00:00+00:00"})
    for sid in ("2330", "2317", "3583"):
        t["watchlist"].append({"group_id": 1, "stock_id": sid, "note": "", "added_at": days[-20] + "T03:00:00+00:00"})
    t["paper_trades"] += [
        {"id": 1, "group_id": 1, "stock_id": "2330", "source": "手動", "buy_date": days[-15], "buy_price": 100.0,
         "shares": 1000, "sell_date": None, "sell_price": None, "note": "", "created_at": days[-15] + "T03:00:00+00:00"},
        {"id": 2, "group_id": 1, "stock_id": "2317", "source": "每日選股", "buy_date": days[-40], "buy_price": 90.0,
         "shares": 2000, "sell_date": days[-10], "sell_price": 95.0, "note": "", "created_at": days[-40] + "T03:00:00+00:00"},
    ]
    # 照規則自動模擬的分組（id 和 AUTO_GROUP_ID 預設值相同；一筆持有中、一筆已賣出）
    t["watch_groups"].append({"id": 11, "owner": "tester", "name": "每日買進測試", "created_at": days[-30] + "T12:00:00+00:00"})
    t["paper_trades"] += [
        {"id": 3, "group_id": 11, "stock_id": "3583", "source": "照規則自動", "buy_date": days[-4], "buy_price": 100.0,
         "shares": 0.95, "sell_date": None, "sell_price": None, "note": f"{days[-5]} 評分第 1 名",
         "created_at": days[-4] + "T12:00:00+00:00"},
        {"id": 4, "group_id": 11, "stock_id": "2330", "source": "照規則自動", "buy_date": days[-25], "buy_price": 100.0,
         "shares": 0.98, "sell_date": days[-15], "sell_price": 103.0, "note": f"{days[-26]} 評分第 2 名",
         "created_at": days[-25] + "T12:00:00+00:00"},
    ]
    # 2330 一筆除息事件，測試還原權值
    t["price_adjustments"].append({"stock_id": "2330", "date": days[-30], "event_type": "除息",
                                   "prev_close": 100.0, "ref_price": 98.0, "factor": 0.98, "source": "test"})
    for code, base in (("TAIEX", 20000), ("TPEX", 250)):
        idx = base * np.exp(np.cumsum(rng.normal(0.0005, 0.01, len(days))))
        for i, d in enumerate(days):
            v = round(float(idx[i]), 2)
            t["market_index"].append({"index_code": code, "date": d, "open": v, "high": v * 1.01, "low": v * 0.99,
                                      "close": v, "change": 0.0, "trade_value": 300_000_000_000})
    for d in days[-60:]:
        for item in ("外資及陸資", "投信", "自營商"):
            net = int(rng.normal(0, 10_000))
            t["futures_institutional"].append({
                "date": d, "contract_code": "臺股期貨", "item": item, "long_oi": 50_000, "long_oi_value": 1,
                "short_oi": 50_000 - net, "short_oi_value": 1, "net_oi": net, "net_oi_value": net})
    # 美股收盤（ETF 資料庫的 us_market_daily，測試時和主資料庫用同一份假資料）
    for sym, name, cat, base in (("^GSPC", "S&P 500", "index", 6000), ("^SOX", "費城半導體", "index", 5000),
                                 ("^VIX", "VIX 恐慌指數", "index", 18), ("TSM", "台積電 ADR", "adr", 30),
                                 ("TWD=X", "美元／台幣", "fx", 32), ("UST10Y", "美國 10 年期公債殖利率", "bond", 4.2)):
        prev = None
        for d in days[-30:]:
            v = round(base * float(np.exp(rng.normal(0, 0.01))), 4)
            t["us_market_daily"].append({
                "symbol": sym, "date": d, "name": name, "category": cat, "close": v, "prev_close": prev,
                "change": None if prev is None else round(v - prev, 4),
                "change_pct": None if prev is None else round((v / prev - 1) * 100, 4)})
            prev = v
    build_etf_tables(t, days, rng)
    return t


ETF_TABLES = ["etf_info", "etf_daily_prices", "etf_price_adjustments", "etf_dividends", "etf_nav", "etf_ins_daily",
              "etf_margin_daily", "etf_sbl_daily", "etf_holder_distribution", "etf_monthly_stats", "etf_top_holdings",
              "etf_expense_ratio", "etf_index_daily"]
# (代號, 名稱, 類別, 市場, 配息頻率, 追蹤指數)
ETFS = [
    ("0050", "元大台灣50", "國內股票", "上市", "半年配", "臺灣50指數"),
    ("00878", "國泰永續高股息", "國內股票", "上市", "季配", None),
    ("00679B", "元大美債20年", "債券", "上櫃", "季配", None),
    ("00632R", "元大台灣50反1", "槓桿反向", "上市", None, None),
]


def build_etf_tables(t, days, rng):
    """ETF 資料庫的假資料（測試時和主資料庫用同一份假資料庫）：0050 有一次分割、每檔都有配息、持股、費用率"""
    today = pd.Timestamp(days[-1])
    months = [p.strftime("%Y-%m") for p in pd.period_range(end=today, periods=13, freq="M")[:-1]]
    for k, (eid, name, cat, market, freq, index) in enumerate(ETFS):
        t["etf_info"].append({"etf_id": eid, "etf_name": name, "market": market, "category": cat, "issuer": "測試投信",
                              "index_name": index, "index_official_name": index, "listing_date": "2010-01-04",
                              "dividend_frequency": freq, "delisted_date": None, "updated_at": days[-1]})
        close = (30 + 20 * k) * np.exp(np.cumsum(rng.normal(0.0004, 0.01, len(days))))
        split_day = days[-120] if eid == "0050" else None
        for i, d in enumerate(days):
            c = round(float(close[i] * (4 if split_day and d < split_day else 1)), 2)
            prev = round(float(close[i - 1] * (4 if split_day and days[i - 1] < split_day else 1)), 2) if i else c
            t["etf_daily_prices"].append({"etf_id": eid, "date": d, "open_price": c, "high_price": round(c * 1.01, 2),
                                          "low_price": round(c * 0.99, 2), "close_price": c, "change": round(c - prev, 2),
                                          "volume": int(rng.integers(1e6, 1e7)), "trade_value": int(c * 5e6),
                                          "transactions": 1000})
        if split_day:
            pre = float(close[days.index(split_day) - 1] * 4)
            t["etf_price_adjustments"].append({"etf_id": eid, "date": split_day, "event_type": "分割", "prev_close": pre,
                                               "ref_price": pre / 4, "factor": 0.25, "source": "test"})
        if freq:
            for j in (-200, -140, -80, -20):
                ex = days[j]
                cash = round(0.3 + 0.1 * k, 3)
                pre = float(close[j - 1])
                t["etf_dividends"].append({"etf_id": eid, "ex_date": ex, "record_date": None,
                                           "pay_date": days[j + 15] if j + 15 < 0 else None, "cash_dividend": cash})
                t["etf_price_adjustments"].append({"etf_id": eid, "date": ex, "event_type": "除息", "prev_close": pre,
                                                   "ref_price": pre - cash, "factor": (pre - cash) / pre, "source": "test"})
            future = (today + pd.Timedelta(days=20 + k)).strftime("%Y-%m-%d")
            t["etf_dividends"].append({"etf_id": eid, "ex_date": future, "record_date": None, "pay_date": None,
                                       "cash_dividend": None if k == 1 else 0.5})
        for i, d in enumerate(days[-3:]):
            c = float(close[len(days) - 3 + i])
            t["etf_nav"].append({"etf_id": eid, "date": d, "market_price": c, "est_nav": c * 0.995,
                                 "premium_pct": 0.5 if k == 2 else 0.05, "nav": None if i == 2 else c * 0.995,
                                 "units": 1_000_000_000 + i * 1_000_000, "units_change": 1_000_000})
        for d in days[-250:]:
            t["etf_ins_daily"].append({"etf_id": eid, "date": d, "foreign_net_shares": int(rng.normal(0, 1e6)),
                                       "sitca_net_shares": int(rng.normal(0, 1e5)), "dealers_net_shares": int(rng.normal(0, 1e5))})
            t["etf_margin_daily"].append({"etf_id": eid, "date": d, "margin_balance": int(rng.integers(1000, 5000)),
                                          "margin_limit": 1_000_000, "margin_rate": 0.3, "short_balance": 10, "short_rate": 0})
            t["etf_sbl_daily"].append({"etf_id": eid, "date": d, "sbl_sell": 1000, "sbl_return": 0, "sbl_adjust": 0,
                                       "sbl_balance": int(rng.integers(1e6, 5e6)), "sbl_limit": 1e7})
        for d in days[-60::5]:
            t["etf_holder_distribution"].append({"etf_id": eid, "week_date": d, "total_holders": 100_000,
                                                 "big_holder_ratio": 20.0, "small_holder_ratio": 40.0})
        for j, ym in enumerate(months):
            if cat != "槓桿反向":
                t["etf_monthly_stats"].append({
                    "etf_id": eid, "year_month": ym, "aum": int(1e11 + j * 1e9), "nav": 30.0, "units": 1_000_000_000,
                    "holders_natural": 100_000 + j * 1000, "holders_legal": 100, "holders_total": 100_100 + j * 1000,
                    "subscription_amount": int(rng.integers(1e8, 1e9)), "redemption_amount": int(rng.integers(1e8, 1e9)),
                    "dca_count": 5000, "dca_accounts": 4000 + j * 10, "dca_amount": int(5e7)})
            for r, (code, hname, htype) in enumerate([("2330", "台積電", "國內上市"), ("2317", "鴻海", "國內上市"),
                                                      ("6488", "環球晶", "國內上櫃"), ("AAPL", "Apple", "國外股票(含承銷)")], 1):
                if j >= len(months) - 2:
                    t["etf_top_holdings"].append({"etf_id": eid, "year_month": ym, "rank": r, "holding_type": htype,
                                                  "holding_code": code, "holding_name": hname, "amount": int(1e9 / r),
                                                  "weight_pct": round(30 / r + j * 0.1, 2)})
        for y in (today.year - 2, today.year - 1):
            t["etf_expense_ratio"].append({"etf_id": eid, "period": str(y), "period_type": "year", "brokerage_pct": 0.05,
                                           "tax_pct": 0.05, "creation_pct": 0.01, "mgmt_pct": 0.15 + 0.1 * k,
                                           "custody_pct": 0.03, "guarantee_pct": 0, "other_pct": 0.01,
                                           "total_pct": round(0.28 + 0.1 * k, 2), "total_amount": int(1e8)})
    for i, d in enumerate(days):
        v = round(15000 * float(np.exp(0.0004 * i)), 2)
        for name, mult in (("臺灣50指數", 1.0), ("臺灣50報酬指數", 1.3)):
            t["etf_index_daily"].append({"date": d, "index_name": name, "close": v * mult, "change": 0, "change_pct": 0,
                                         "index_type": "價格", "category": "證交所"})


def signal_performance(client, top_n=20, horizon=5):
    """假的 signal_performance RPC：每個評分日一筆"""
    dates = sorted({r["date"] for r in client.tables.get("daily_signals", [])})
    return [{"date": d, "fwd_date": d, "top_return": 1.5, "market_return": 0.8, "top_count": top_n}
            for d in dates[:-horizon]]


def _recent_series(client, table, start_date, fields):
    """假的 recent_prices／recent_ins RPC：每檔股票一列，日期與數值放在陣列裡"""
    series = {}
    for r in sorted(client.tables.get(table, []), key=lambda r: (r["stock_id"], r["date"])):
        if r["date"] >= start_date:
            row = series.setdefault(r["stock_id"], {"stock_id": r["stock_id"], "dates": [],
                                                    **{name: [] for name in fields}})
            row["dates"].append(r["date"])
            for name, col in fields.items():
                row[name].append(r.get(col))
    return [series[k] for k in sorted(series)]


def recent_prices(client, start_date):
    return _recent_series(client, "daily_prices", start_date, {"closes": "close_price", "volumes": "volume"})


def recent_ins(client, start_date):
    return _recent_series(client, "stock_ins_daily", start_date,
                          {"foreign_net": "foreign_net_shares", "sitca_net": "sitca_net_shares"})
