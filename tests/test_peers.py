"""同產業比較：同業名次、前幾名加上這檔、外資近 5 日合計與 20 日漲跌的中位數"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from utils.card import peer_comparison  # noqa: E402


def _signals(n):
    return pd.DataFrame({"stock_id": [f"{i:04d}" for i in range(1, n + 1)],
                         "stock_name": [f"股{i}" for i in range(1, n + 1)],
                         "total_score": [100 - i for i in range(1, n + 1)], "rank": range(1, n + 1),
                         "change_5d": 1.0, "change_20d": [float(i) for i in range(1, n + 1)]})


def test_rank_top_and_self_appended():
    days = [f"2026-10-{d:02d}" for d in range(20, 27)]
    ins = pd.DataFrame([{"stock_id": "0012", "date": d, "foreign_net_shares": 1000} for d in days])
    table, s = peer_comparison("0012", _signals(15), ins, top=10)
    assert list(table["代號"]) == [f"{i:04d}" for i in range(1, 11)] + ["0012"]
    assert s == {"檔數": 15, "名次": 12, "20日%": 12.0, "同業20日中位數": 8.0}
    assert table.iloc[-1]["外資5日(張)"] == 5  # 只加最近 5 天
    assert pd.isna(table.iloc[0]["外資5日(張)"])


def test_in_top_and_missing():
    table, s = peer_comparison("0002", _signals(3), pd.DataFrame())
    assert len(table) == 3 and s["名次"] == 2
    table, s = peer_comparison("9999", _signals(3), None)
    assert table.empty and s is None
    assert peer_comparison("0001", pd.DataFrame(), None)[1] is None
