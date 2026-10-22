"""選股頁的買賣規則模擬"""
import pandas as pd


def test_replay_buys_top_and_sells_after_hold():
    from utils.picks import replay
    dates = [f"d{i:02d}" for i in range(14)]
    tops = {d: ["A", "B", "C"] for d in dates}
    ok = {d: True for d in dates}
    trades, plan = replay(dates, tops, ok, slots=2, hold=3)
    first = [t for t in trades if t["buy_date"] == "d01"]
    assert [t["stock_id"] for t in first] == ["A", "B"]          # 名額 2，依排名買前兩檔
    assert all(t["sell_date"] == "d04" for t in first)            # 第 1 天買、第 4 天開盤賣（持有 3 天）
    # 賣出當天開盤用前一天的排名補回來，同一檔可以再買
    assert sorted(t["stock_id"] for t in trades if t["buy_date"] == "d04") == ["A", "B"]
    # 最後一次在 d13 買進，明天（第 14 天）還沒到期
    assert plan["sell"] == [] and plan["buy"] == [] and plan["free"] == 0
    assert {p["day"] for p in plan["hold"]} == {1}


def test_replay_market_filter_and_plan():
    from utils.picks import replay
    dates = ["d0", "d1", "d2", "d3"]
    tops = {"d0": ["A"], "d1": ["B"], "d2": ["C"], "d3": ["D", "B"]}
    ok = {"d0": False, "d1": True, "d2": False, "d3": True}
    trades, plan = replay(dates, tops, ok, slots=2, hold=2)
    assert [(t["stock_id"], t["buy_date"]) for t in trades] == [("B", "d2")]  # d0、d2 在季線下不買
    assert [p["stock_id"] for p in plan["sell"]] == ["B"]                     # d2 買、第 2 天後明天開盤賣
    assert plan["buy"] == ["D", "B"] and plan["market_ok"]                    # 賣掉的 B 明天又在名單上


def test_market_ok_by_date():
    from utils.picks import market_ok_by_date
    idx = pd.DataFrame({"date": [f"2026-01-{i:02d}" for i in range(1, 6)], "close": [10, 10, 10, 13, 7]})
    ok = market_ok_by_date(idx, ma_days=3)
    assert ok == {"2026-01-01": False, "2026-01-02": False, "2026-01-03": False,
                  "2026-01-04": True, "2026-01-05": False}


def test_trade_returns_uses_open_and_last_close():
    from utils.picks import trade_returns
    trades = [{"stock_id": "A", "signal_date": "d0", "buy_date": "d1", "buy_i": 1, "sell_date": "d3"},
              {"stock_id": "B", "signal_date": "d1", "buy_date": "d2", "buy_i": 2, "sell_date": None, "day": 2}]
    prices = pd.DataFrame({"stock_id": ["A", "A", "B", "B"], "date": ["d1", "d3", "d2", "d3"],
                           "open_price": [100, 110, 50, 52], "close_price": [101, 111, 51, 55]})
    df = trade_returns(trades, prices, 0.5).set_index("stock_id")
    assert round(df.at["A", "ret"], 2) == 9.5      # 開盤 100 買、開盤 110 賣
    assert round(df.at["B", "ret"], 2) == 9.5      # 開盤 50 買、最新收盤 55


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
