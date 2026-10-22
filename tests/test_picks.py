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
    prices = pd.DataFrame({"stock_id": ["A", "A", "B", "B"], "date": ["d1", "d3", "d3", "d4"],
                           "open_price": [100, 110, 50, 51], "close_price": [101, 111, 51, 55]})
    rec = pick_record(dates, ranks, {"d0": True, "d2": False}, prices, hold=2, cost_pct=0.5).set_index("stock_id")
    assert rec.at["A", "buy_price"] == 100 and rec.at["A", "sell_price"] == 110
    assert "C" not in rec.index                                  # 最新評分日還沒買進
    assert rec.at["A", "status"] == "已賣出" and rec.at["A", "sell_date"] == "d3"
    assert round(rec.at["A", "ret"], 2) == 9.5                   # d1 開盤 100 買、d3 開盤 110 賣
    assert rec.at["B", "status"] == "持有第 2 / 2 天" and not rec.at["B", "market_ok"]
    assert round(rec.at["B", "ret"], 2) == 9.5                   # d3 開盤 50 買、最新收盤 55
    assert rec.at["B", "last_close"] == 55 and pd.isna(rec.at["B", "sell_price"])
