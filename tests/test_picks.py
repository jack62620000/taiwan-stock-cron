"""選股頁的參考標籤"""
import pandas as pd


def test_stock_tags():
    import numpy as np
    from utils.picks import stock_tags
    days = pd.bdate_range("2026-01-01", periods=150).strftime("%Y-%m-%d")
    up = 100 * np.exp(np.linspace(0, 0.5, 150))
    prices = pd.DataFrame({"stock_id": ["A"] * 150 + ["B"] * 150, "date": list(days) * 2,
                           "close_price": list(up) + list(up[::-1])})
    mom = pd.DataFrame({"stock_id": ["A", "B"], "trend_template": [True, False], "breakout": [True, False],
                        "pullback": [False, False], "close_price": [140.0, 100.0], "ma50": [100.0, 100.0]})
    tags = stock_tags(["A", "B"], prices, mom)
    assert "🚀 趨勢模板" in tags["A"] and "🚀 新高量增" in tags["A"] and "⚠️ 漲多" in tags["A"]
    assert "🚀 趨勢模板" not in tags["B"]


def test_market_ok_by_date():
    from utils.picks import market_ok_by_date
    idx = pd.DataFrame({"date": [f"2026-01-{i:02d}" for i in range(1, 6)], "close": [10, 10, 10, 13, 7]})
    ok = market_ok_by_date(idx, ma_days=3)
    assert ok == {"2026-01-01": False, "2026-01-02": False, "2026-01-03": False,
                  "2026-01-04": True, "2026-01-05": False}


def test_pick_record():
    from utils.picks import pick_record
    dates = ["d0", "d1", "d2", "d3", "d4"]
    ranks = pd.DataFrame({"date": ["d0", "d2", "d4"], "stock_id": ["A", "B", "C"], "rank": [1, 1, 1],
                          "total_score": [90, 80, 70]})
    prices = pd.DataFrame({"stock_id": ["A", "A", "A", "B", "B", "B"], "date": ["d0", "d1", "d3", "d2", "d3", "d4"],
                           "open_price": [103, 100, 110, 49, 50, 51], "close_price": [103, 101, 111, 49.5, 51, 55]})
    rec = pick_record(dates, ranks, {"d0": True, "d2": False}, prices, hold=2, cost_pct=0.5).set_index("stock_id")
    assert rec.at["A", "buy_price"] == 100 and rec.at["A", "sell_price"] == 110
    assert "C" not in rec.index                                  # 最新評分日還沒買進
    assert rec.at["A", "status"] == "已賣出" and rec.at["A", "sell_date"] == "d3"
    assert round(rec.at["A", "ret"], 2) == 9.5                   # d1 開盤 100 買、d3 開盤 110 賣
    assert rec.at["B", "status"] == "持有第 2 / 2 天" and not rec.at["B", "market_ok"]
    assert round(rec.at["B", "ret"], 2) == 9.5                   # d3 開盤 50 買、最新收盤 55
    assert rec.at["B", "last_close"] == 55 and pd.isna(rec.at["B", "sell_price"])
    assert rec.at["A", "open_zone"] == "較佳" and round(rec.at["A", "open_gap"], 2) == -2.91  # 收 103、開 100
    assert rec.at["B", "open_zone"] == "不追" and round(rec.at["B", "open_gap"], 2) == 1.01   # 收 49.5、開 50


def test_open_zone():
    from utils.picks import open_zone
    assert open_zone(100, 98) == "較佳" and open_zone(100, 102) == "不追" and open_zone(100, 100) == "其他"
    assert open_zone(100, 104) == "其他" and open_zone(None, 100) is None and open_zone(100, None) is None


def test_sector_table_ignores_missing_returns():
    """新上市股票沒有 20 日漲幅時，不能把產業平均拉向 0"""
    from utils.sector import sector_table
    df = pd.DataFrame({"stock_id": ["A", "B", "C"], "industry": ["半導體"] * 3,
                       "5日%": [10.0, 10.0, None], "20日%": [10.0, 10.0, None], "均成交值": [1.0, 1.0, 8.0]})
    for weighting in ("等權重", "成交值加權"):
        row = sector_table(df, weighting).iloc[0]
        assert round(row["20日%"], 6) == 10.0 and round(row["5日%"], 6) == 10.0


def test_evaluate_trades_div_mult():
    """持有期間除息：div_mult 記下還原比例，模擬持股頁用它換算停損價"""
    from utils.paper import evaluate_trades
    trades = pd.DataFrame({"stock_id": ["A"], "buy_date": ["2026-10-01"], "buy_price": [100.0], "shares": [1.0],
                           "sell_date": [None], "sell_price": [None]})
    raw = pd.DataFrame({"stock_id": ["A", "A"], "date": ["2026-10-01", "2026-10-02"], "close_price": [100.0, 95.0]})
    adj = raw.assign(close_price=[95.0, 95.0])  # 10/2 除息 5 元：還原後 10/1 是 95
    out = evaluate_trades(trades, raw, adj)
    assert round(out.at[0, "div_mult"], 6) == round(1 / 0.95, 6)
    assert round(out.at[0, "end_price"] * out.at[0, "div_mult"], 6) == 100.0


def test_open_ref():
    """明天開盤參考價：較佳價往下取、不追區間對齊升降單位"""
    from utils.picks import open_ref, tick_size
    assert tick_size(9.99) == 0.01 and tick_size(50) == 0.1 and tick_size(1000) == 5.0
    assert open_ref(100) == (98.0, 101.0, 103.0)
    good, lo, hi = open_ref(1085)  # 1085 × 0.98 = 1063.3 → 1060（千元以上跳 5 元）；×1.01 = 1095.85 → 1100；×1.03 = 1117.55 → 1115
    assert (good, lo, hi) == (1060.0, 1100.0, 1115.0)
    assert open_ref(None) == (None, None, None)
