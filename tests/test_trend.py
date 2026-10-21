"""技術指標近 5 日走向：往上、往下、持平與資料不足"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from utils.card import TREND_DAYS, indicator_trend, trend_text  # noqa: E402


def test_up_down_flat():
    assert indicator_trend(pd.Series([30, 31, 32, 33, 34, 40]), 2) == ("↑", 10)
    assert indicator_trend(pd.Series([70, 68, 66, 64, 62, 60]), 2) == ("↓", -10)
    assert indicator_trend(pd.Series([50, 51, 49, 50, 52, 51]), 2)[0] == "→"


def test_relative_flat_and_short_data():
    assert indicator_trend(pd.Series([1.00, 0, 0, 0, 0, 1.02]), 0.05, relative=True)[0] == "→"
    assert indicator_trend(pd.Series([-0.5, 0, 0, 0, 0, 0.5]), 0.05, relative=True)[0] == "↑"
    assert indicator_trend(pd.Series([1, 2, 3]), 1) is None
    assert indicator_trend(pd.Series([None] * 10), 1) is None


def test_trend_text():
    assert trend_text(("↑", 6.24)) == f"{TREND_DAYS} 日往上 ↑ +6.2"
    assert trend_text(("↓", -0.123), "MACD") == f"{TREND_DAYS} 日往下 ↓ -0.12"
    assert trend_text(("↑", 1.5), "均線排列") == f"月線{TREND_DAYS} 日往上 ↑ +1.50 元"
    assert trend_text(None) == ""


def test_rsi_kd_zone():
    """高檔是強勢區、低檔是弱勢區（回測顯示高檔之後反而比較強）"""
    from utils.indicators import STRONG, WEAK, NORMAL, zone
    assert zone(75, 70, 30) == STRONG == "強勢區"
    assert zone(25, 70, 30) == WEAK == "弱勢區"
    assert zone(50, 70, 30) == NORMAL


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
