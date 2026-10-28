"""個股分析卡的互動 K 線圖設定（utils.kline.build_charts）"""
import numpy as np
import pandas as pd
import pytest

from utils.indicators import calc_all
from utils.kline import SUB_CHARTS, build_charts


@pytest.fixture
def prices():
    d = pd.bdate_range("2026-01-01", periods=200).strftime("%Y-%m-%d")
    c = 100 * np.exp(np.cumsum(np.random.default_rng(3).normal(0.001, 0.02, len(d))))
    df = pd.DataFrame({"stock_id": "2330", "date": d, "open_price": c * 0.995, "high_price": c * 1.02,
                       "low_price": c * 0.98, "close_price": c, "volume": 3_000_000})
    df.loc[150, ["open_price", "high_price", "low_price", "close_price"]] = None   # 一天沒有成交
    return calc_all(df)   # 沒有成交的日子由 calc_all 補成股價不變並標記 no_trade


def test_main_volume_and_sub_charts(prices):
    ins = pd.DataFrame({"date": prices["date"], "foreign_net_shares": 500_000})
    for sub in SUB_CHARTS:
        charts = build_charts(prices, 120, sub=sub, ins=ins)
        assert len(charts) == 3
        candles = charts[0]["series"][0]
        assert candles["type"] == "Candlestick" and len(candles["data"]) == 120
        assert [s["options"]["title"] for s in charts[0]["series"][1:]] == ["MA5", "MA20", "MA60"]
        assert len(charts[1]["series"][0]["data"]) == 120
        # 只有最下面一張顯示日期
        assert [c["chart"]["timeScale"]["visible"] for c in charts] == [False, False, True]


def test_no_trade_day_is_gray(prices):
    charts = build_charts(prices, 60, sub=None)
    assert len(charts) == 2 and charts[1]["chart"]["timeScale"]["visible"]
    day = prices["date"].iloc[150]
    bar = next(b for b in charts[0]["series"][0]["data"] if b["time"] == day)
    assert bar["color"] == "#9ca3af"


def test_levels_and_markers(prices):
    charts = build_charts(prices, 120, overlays=("布林通道",), levels={"停損價": (95.5, "#ea580c"), "空的": (None, "#000")})
    titles = [s["options"]["title"] for s in charts[0]["series"][1:]]
    assert titles == ["停損價", "布林上軌", "布林下軌"]
    level = charts[0]["series"][1]["data"]
    assert len(level) == 120 and {p["value"] for p in level} == {95.5}
    marks = charts[0]["series"][0]["markers"]
    assert marks and all(m["time"] in set(prices["date"].tail(120)) for m in marks)
    assert {m["shape"] for m in marks} <= {"arrowUp", "arrowDown", "circle"}
    assert "markers" not in build_charts(prices, 120, markers=False)[0]["series"][0]
