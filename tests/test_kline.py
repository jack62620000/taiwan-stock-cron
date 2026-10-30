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
    assert titles == ["布林上軌", "布林中軌", "布林下軌"]
    assert charts[0]["series"][0]["priceLines"] == [
        {"price": 95.5, "color": "#ea580c", "lineWidth": 1, "lineStyle": 2, "axisLabelVisible": True, "title": "停損價"}]
    marks = charts[0]["series"][0]["markers"]
    assert marks and all(m["time"] in set(prices["date"].tail(120)) for m in marks)
    assert {m["shape"] for m in marks} <= {"arrowUp", "arrowDown", "circle"}
    assert all(m["text"] == "" and m["label"][0] in "買賣過" for m in marks)
    assert "markers" not in build_charts(prices, 120, markers=False)[0]["series"][0]


def test_render_html(prices):
    from utils.kline import render_html
    charts = build_charts(prices, 60)
    html, height = render_html(charts)
    assert "TradingView Lightweight Charts" in html and "__CONF__" not in html and "__LIB__" not in html
    assert height == sum(c["chart"]["height"] for c in charts) + 52


def test_event_markers(prices):
    """除權息、減資標在成交量上方，左上角說明除息金額；圖範圍外的事件不標"""
    d = prices["date"]
    events = pd.DataFrame({"date": [d.iloc[0], d.iloc[-10], d.iloc[-5]],
                           "event_type": ["除權息", "除權息", "減資"],
                           "prev_close": [100.0, 50.0, 20.0], "ref_price": [97.0, 48.5, 25.0]})
    charts = build_charts(prices, 60, events=events)
    marks = charts[1]["series"][0]["markers"]
    assert [m["time"] for m in marks] == [d.iloc[-10], d.iloc[-5]]
    assert marks[0]["kind"] == "除權息" and marks[0]["label"] == "−1.50 元（-3.0%）"
    assert marks[1]["kind"] == "減資" and "參考價 25.00" in marks[1]["label"]
    assert "markers" not in build_charts(prices, 60)[1]["series"][0]


def test_resample_weekly_monthly():
    from utils.kline import align_to_bars, resample_bars, resample_ins
    days = ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-09", "2026-10-12", "2026-10-13", "2026-11-02"]
    prices = pd.DataFrame({"date": days,
                           "open_price": [10, 11, None, 12, 20, 21, 30],
                           "high_price": [11, 13, None, 12.5, 22, 23, 31],
                           "low_price": [9, 10.5, None, 11.5, 19, 20, 29],
                           "close_price": [10.5, 12, None, 12.2, 21, 22, 30.5],
                           "volume": [1000, 2000, None, 3000, 4000, 5000, 6000]})
    w = resample_bars(prices, "W-FRI")
    assert list(w["date"]) == ["2026-10-09", "2026-10-13", "2026-11-02"]   # 日期是那週最後一個交易日（含沒成交的日子）
    assert w.iloc[0][["open_price", "high_price", "low_price", "close_price", "volume"]].tolist() == [10, 13, 9, 12.2, 6000]
    m = resample_bars(prices, "M")
    assert list(m["date"]) == ["2026-10-13", "2026-11-02"] and m.iloc[0]["open_price"] == 10 and m.iloc[0]["close_price"] == 22
    ins = pd.DataFrame({"date": days, "foreign_net_shares": [1000, 2000, 0, -500, 4000, 0, 7000]})
    wi = resample_ins(ins, w, "W-FRI")
    assert wi.set_index("date")["foreign_net_shares"].to_dict() == {"2026-10-09": 2500, "2026-10-13": 4000, "2026-11-02": 7000}
    ev = pd.DataFrame({"date": ["2026-10-07", "2025-01-02"], "event_type": ["除息", "除息"]})
    assert list(align_to_bars(ev, w, "W-FRI")["date"]) == ["2026-10-09"]   # 圖上範圍以外的事件拿掉


def test_sub_chart_keeps_bar_count_when_ins_starts_later(prices):
    """法人資料比股價晚開始時，副圖補空白點，K 棒、成交量、外資買賣超的根數一樣，上下才會對齊"""
    ins = pd.DataFrame({"date": prices["date"].iloc[-100:], "foreign_net_shares": -500_000})
    charts = build_charts(prices, 120, sub="外資買賣超", ins=ins)
    times = [[d["time"] for d in c["series"][0]["data"]] for c in charts]
    assert times[0] == times[1] == times[2] and len(times[0]) == 120
    sub = charts[2]["series"][0]["data"]
    assert all("value" not in d for d in sub[:20]) and all("value" in d for d in sub[20:])


def test_empty_sub_chart_is_dropped(prices):
    """副圖整段沒有數值（例如月線 K 棒太少算不出 MACD）就不畫，成交量圖改顯示時間軸"""
    df = prices.tail(6).assign(MACD_HIST=np.nan)
    charts = build_charts(df, 6, sub="MACD", markers=False)
    assert len(charts) == 2
    assert charts[1]["chart"]["timeScale"]["visible"] is True
