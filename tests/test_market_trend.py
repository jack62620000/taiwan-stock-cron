import numpy as np
import pandas as pd

from utils.market_trend import analyze, foreign_amount, last_change, trend_text, verdict_history


def _index(closes):
    dates = pd.bdate_range("2025-01-01", periods=len(closes)).strftime("%Y-%m-%d")
    c = pd.Series(closes, dtype=float)
    return pd.DataFrame({"date": dates, "close": c, "high": c * 1.005, "trade_value": 5e11})


def _futures(values):
    dates = pd.bdate_range("2025-01-01", periods=len(values)).strftime("%Y-%m-%d")
    return pd.DataFrame({"date": dates, "item": "外資及陸資", "net_oi": values})


def test_uptrend_pullback():
    # 一路漲 240 天後回檔 6%：長多格局中的回檔，濾網紅燈
    up = list(np.linspace(20000, 40000, 250))
    closes = up + list(np.linspace(40000, 37600, 10))
    foreign = pd.Series([-300.0] * 19 + [50.0], index=[f"d{i:02d}" for i in range(20)])
    r = analyze(_index(closes), foreign, _futures([-80000 + i * 100 for i in range(30)]))
    assert r["verdict"] == "長多格局中的回檔" and r["tone"] == 0
    items = {k: (v, t) for k, v, t in r["items"]}
    assert "近 5 日賣超 1,150 億" in items["外資現股"][0] and items["外資現股"][1] == -1
    assert "淨空單 77,100 口，近 5 日空單減少 500 口" in items["外資期貨"][0]
    assert "紅燈" in items["選股濾網"][0] and "支撐" in items["關鍵價位"][0]


def test_bull_and_bear():
    bull = analyze(_index(list(np.linspace(20000, 40000, 260))))
    assert bull["verdict"] == "多頭" and bull["tone"] == 1
    assert "綠燈" in dict((k, v) for k, v, _ in bull["items"])["選股濾網"]
    bear = analyze(_index(list(np.linspace(40000, 20000, 260))))
    assert bear["verdict"] == "空頭" and bear["tone"] == -1
    assert analyze(_index([100.0] * 30)) is None


def test_foreign_amount():
    ins = pd.DataFrame({"stock_id": ["A", "B", "A"], "date": ["d1", "d1", "d2"],
                        "foreign_net_shares": [1_000_000, -2_000_000, 500_000]})
    closes = pd.DataFrame({"stock_id": ["A", "B", "A"], "date": ["d1", "d1", "d2"], "close_price": [500, 100, 600]})
    amt = foreign_amount(ins, closes)
    assert list(amt.round(2)) == [3.0, 3.0]   # (5 億 − 2 億)、3 億


def test_trend_text():
    r = analyze(_index(list(np.linspace(20000, 40000, 260))))
    text = trend_text(r)
    assert text.startswith("🧭 大盤趨勢（") and "🔴 選股濾網：" in text and "\n" in text
    assert trend_text(None) == "大盤資料不足，暫時無法分析趨勢。"


def test_verdict_history():
    # 一路漲 250 天後回檔：色帶最後一天和今天的結論一樣，而且看得到從多頭轉成回檔
    closes = list(np.linspace(20000, 40000, 300)) + list(np.linspace(40000, 37600, 10))
    r = analyze(_index(closes))
    hist = r["history"]
    assert len(hist) == 60 and hist[-1][0] == r["date"] and hist[-1][1] == r["verdict"]
    assert hist[0][1] == "多頭"
    day, before, after = last_change(hist)
    assert before == "多頭" and after != "多頭"
    assert "最近一次轉變" in trend_text(r)
    assert last_change([("d1", "盤整", 0), ("d2", "盤整", 0)]) is None
    assert verdict_history(_index([100.0] * 30)) == []
