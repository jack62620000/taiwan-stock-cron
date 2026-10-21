"""技術指標近 5 日走向：往上、往下、持平與資料不足"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from utils.card import TREND_DAYS, indicator_trend, trend_text  # noqa: E402


def test_up_down_flat():
    assert indicator_trend(pd.Series([30, 31, 32, 33, 34, 40]), 2) == ("↑", 10, 30, 40)
    assert indicator_trend(pd.Series([70, 68, 66, 64, 62, 60]), 2) == ("↓", -10, 70, 60)
    assert indicator_trend(pd.Series([50, 51, 49, 50, 52, 51]), 2)[0] == "→"


def test_relative_flat_and_short_data():
    assert indicator_trend(pd.Series([1.00, 0, 0, 0, 0, 1.02]), 0.05, relative=True)[0] == "→"
    assert indicator_trend(pd.Series([-0.5, 0, 0, 0, 0, 0.5]), 0.05, relative=True)[0] == "↑"
    assert indicator_trend(pd.Series([1, 2, 3]), 1) is None
    assert indicator_trend(pd.Series([None] * 10), 1) is None


def test_trend_text():
    """寫清楚是「現在的值」和 5 日前的值比，並列出前後數值"""
    assert trend_text(("↓", -15.8, 60.2, 44.4)) == f"↓ 比 {TREND_DAYS} 日前少 15.8（60.2 → 44.4）"
    assert trend_text(("↑", 6.24, 30, 36.24), "布林通道") == f"↑ 比 {TREND_DAYS} 日前多 6 個百分點（30% → 36%）"
    assert trend_text(("↑", 1.5, 100, 101.5), "均線排列") == f"↑ 月線比 {TREND_DAYS} 日前多 1.50 元（100.00 → 101.50）"
    assert trend_text(("→", 0.1, 50, 50.1)) == f"→ 和 {TREND_DAYS} 日前差不多（50.0 → 50.1）"
    assert trend_text(None) == ""


def test_cross_state():
    from utils.card import cross_state
    assert cross_state(pd.Series([-1, -2, 1, 2, 3]), "K", "D") == ("↑", "K 在 D 上方，往上（2 天前黃金交叉）")
    assert cross_state(pd.Series([1, 2, -1]), "K", "D") == ("↓", "K 在 D 下方，往下（今天死亡交叉）")
    assert cross_state(pd.Series([1, 2, 3]), "K", "D") == ("↑", "K 在 D 上方，往上")  # 期間內沒交叉過
    assert cross_state(pd.Series([1.0]), "K", "D") is None


def test_rsi_five_levels():
    from utils.indicators import LAMP, rsi_level
    assert [rsi_level(v)[0] for v in (95, 90, 80, 60, 40, 30, 10)] == ["超買", "超買", "強勢", "偏強", "偏弱", "偏弱", "超賣"]
    assert rsi_level(float("nan")) == ("N/A", "")
    assert LAMP["超買"] == "bear" and LAMP["強勢"] == "bull" and LAMP["偏強"] == "neu"


def test_kd_five_levels():
    from utils.indicators import KD_LEVELS, level
    names = [level(KD_LEVELS, v)[:2] for v in (95, 85, 60, 30, 10)]
    assert names == [("超買", "bear"), ("強勢", "bull"), ("偏強", "neu"), ("偏弱", "neu"), ("超賣", "bear")]
    assert level(KD_LEVELS, float("nan")) == ("N/A", "neu", "")


def test_other_levels():
    """位階、布林、乖離依回測分級：高檔偏多（紅），低檔偏弱（綠）"""
    from utils.indicators import POSITION_LEVELS, BOLL_LEVELS, BIAS_LEVELS, level, volume_level
    assert [level(POSITION_LEVELS, v)[:2] for v in (95, 80, 60, 30, 5)] == [
        ("接近年高", "bull"), ("偏高", "bull"), ("中上段", "neu"), ("中下段", "bear"), ("接近年低", "bear")]
    assert [level(BOLL_LEVELS, v)[0] for v in (120, 90, 50, 10, -5)] == [
        "突破上軌", "靠近上軌", "通道中段", "靠近下軌", "跌破下軌"]
    assert level(BOLL_LEVELS, 10)[1] == "bear" and level(BOLL_LEVELS, 90)[1] == "bull"
    assert [level(BIAS_LEVELS, v)[:2] for v in (15, 7, 2, -5, -15)] == [
        ("大幅正乖離", "bull"), ("正乖離", "neu"), ("站上月線", "neu"), ("跌破月線", "bear"), ("大幅負乖離", "neu")]
    assert volume_level(3.5, -1)[0] == "爆量"
    assert volume_level(2, 1)[:2] == ("價漲量增", "bull")
    assert volume_level(2, -1)[:2] == ("價跌量增", "neu")
    assert volume_level(0.5, 1)[0] == "量縮" and volume_level(1, 1)[0] == "量能正常"


def test_chip_trend():
    from utils.card import chip_trend
    c = chip_trend(pd.Series([-1000] * 5 + [-500, 2000, 3000, 1000, 4000]))  # 前 5 日賣、近 5 日轉買
    assert (c["label"], c["arrow"], c["streak"]) == ("由賣轉買", "↑", "連買 4 天")
    assert c["last5"] == 9.5 and c["prev5"] == -5 and c["sum10"] == 4.5
    assert chip_trend(pd.Series([1000] * 5 + [3000] * 5))["label"] == "買超加速"
    assert chip_trend(pd.Series([3000] * 5 + [1000] * 5))["label"] == "買超減緩"
    assert chip_trend(pd.Series([2000] * 5 + [-1000] * 5))["label"] == "由買轉賣"
    assert chip_trend(pd.Series([-1000] * 5 + [-3000] * 5))["label"] == "賣超加速"


def test_balance_trend_and_hint():
    from utils.card import balance_trend, margin_hint
    t = balance_trend(pd.Series(range(100, 130)))  # 每天多 1 張
    assert t["chg5"] == 5 and t["chg20"] == 20 and t["streak"] == "連增 29 天"
    assert balance_trend(pd.Series([100])) is None
    assert margin_hint(100, -5).startswith("股價跌、融資卻增加")
    assert margin_hint(-100, 5).startswith("股價漲、融資減少")
    assert margin_hint(None, 5) == ""


def test_revenue_trend():
    from utils.card import revenue_trend
    df = pd.DataFrame({
        "year_month": [f"2026-{m:02d}" for m in range(1, 8)],
        "revenue": [100, 110, 120, 130, 140, 150, 160],
        "last_year_revenue": [100, 100, 100, 100, 100, 100, 100],
        "yoy_growth": [-1, 10, 20, 30, 40, 50, 60],
        "cumulative_revenue": [None] * 6 + [910],
        "cumulative_yoy_growth": [None] * 6 + [30],
    })
    r = revenue_trend(df)
    assert round(r["yoy3"], 2) == 50 and round(r["yoy3_prev"], 2) == 20
    assert r["rank12"] == 1 and r["n12"] == 7
    assert r["yoy_streak"] == "連續 6 個月年增"
    assert r["cum_yoy"] == 30 and len(r["bars"]) == 7
    assert revenue_trend(pd.DataFrame())["yoy3"] is None


def test_obv_state():
    from utils.indicators import obv_state
    up = pd.Series(range(1, 31), dtype=float)
    assert obv_state(up, up)[:2] == ("量價齊揚", "bull")
    # OBV 先創 20 日新低、股價還在中間
    price = pd.Series([10.0] * 25 + [11, 9, 10, 10, 10])
    obv = pd.Series([float(30 - i) for i in range(30)])
    assert obv_state(price, obv)[:2] == ("OBV 先破底", "bear")
    assert obv_state(up, pd.concat([up.iloc[:-1], pd.Series([15.0])], ignore_index=True))[0] == "頂背離"
    assert obv_state(pd.Series([1.0] * 5), pd.Series([1.0] * 5)) is None


def test_macd_zero():
    from utils.indicators import macd_zero
    assert macd_zero(1, 0.5) == ("零軸上，多頭", "bull")
    assert macd_zero(-1, -0.5) == ("零軸下，空頭", "bear")
    assert macd_zero(0.1, -0.2)[1] == "neu"
    assert macd_zero(None, 1) is None


def test_backtest_lamps():
    from utils.card import chip_lamp, big_holder_lamp, revenue_lamps, chip_trend, SHORT_RATIO_LEVELS
    from utils.indicators import level
    buy = chip_trend(pd.Series([1000] * 10))  # 連買 10 天、買超持平 → 買超減緩
    sell = chip_trend(pd.Series([-1000] * 10))
    assert chip_lamp("外資", buy)[0] == "bull" and chip_lamp("外資", sell)[0] == "bear"
    assert chip_lamp("投信", buy)[0] == "bull" and chip_lamp("投信", sell)[0] == "neu"
    assert chip_lamp("自營商", buy)[0] == "neu"
    assert chip_lamp("外資", chip_trend(pd.Series([-1000] * 5 + [1000] * 5)))[0] == "neu"  # 由賣轉買
    assert big_holder_lamp(1.2)[0] == "bull" and big_holder_lamp(-2)[0] == "neu" and big_holder_lamp(None)[0] == "neu"
    assert level(SHORT_RATIO_LEVELS, 8)[1] == "bull" and level(SHORT_RATIO_LEVELS, 1)[1] == "neu"
    rv = {"yoy3": 15.0, "cum_yoy": -3.0, "rank12": 1, "yoy_streak_days": 4}
    r = revenue_lamps(25.0, rv)
    assert r["yoy"][:2] == ("高成長", "bull") and r["yoy3"][1] == "bull" and r["cum"][1] == "bear"
    assert r["rank"][0] == "bull" and r["streak"][0] == "bull"
    r = revenue_lamps(None, {"yoy3": None, "cum_yoy": None, "rank12": 3, "yoy_streak_days": -1})
    assert r["yoy"][0] == "N/A" and r["rank"][0] == "neu" and r["streak"][0] == "bear"


def test_lamp_summary():
    from utils.card import lamp_summary, margin_hint_lamp
    s = lamp_summary(["bull", "bull", "neu"])
    assert (s["red"], s["green"], s["gold"], s["total"], s["lamp"], s["verdict"]) == (2, 0, 1, 3, "bull", "整體偏多")
    assert lamp_summary(["bull", "bear"])["verdict"] == "多空交錯"
    assert lamp_summary(["bear", "bear"])["lamp"] == "bear" and lamp_summary([])["total"] == 0
    assert margin_hint_lamp(100, 5) == "bull" and margin_hint_lamp(100, -5) == "bear"
    assert margin_hint_lamp(-100, 5) == "neu" and margin_hint_lamp(-100, -5) == "bear" and margin_hint_lamp(None, 5) is None
