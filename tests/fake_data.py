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
                "new_high": up, "breakout": up and j in (1, 4), "liquid": True})
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
    return t


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
