"""對照公開來源抽查：比對邏輯（不連網路）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import verify_public as v  # noqa: E402


def test_compare_finds_missing_extra_and_diffs():
    source = [
        {"stock_id": "2330", "date": "2026-10-28", "close_price": 1450.0, "volume": 30000000},
        {"stock_id": "2303", "date": "2026-10-28", "close_price": 50.1, "volume": 1000},
        {"stock_id": "1101", "date": "2026-10-28", "close_price": None, "volume": 0},
    ]
    db = [
        # 資料庫的數字是字串、多了來源沒有的欄位（不比）
        {"stock_id": "2330", "date": "2026-10-28", "close_price": "1450.00", "volume": 30000000, "extra": 1},
        {"stock_id": "2303", "date": "2026-10-28", "close_price": "50.2", "volume": 1000},
        {"stock_id": "9999", "date": "2026-10-28", "close_price": "10", "volume": 1},
    ]
    missing, extra, diffs = v.compare(source, db, ("stock_id", "date"))
    assert missing == [("1101", "2026-10-28")]
    assert extra == [("9999", "2026-10-28")]
    assert diffs == [(("2303", "2026-10-28"), "close_price", 50.1, "50.2")]


def test_compare_limits_columns_and_treats_nulls():
    source = [{"stock_id": "2330", "year_month": "2026-09", "revenue": 100, "note": "新備註", "mom_growth": None}]
    db = [{"stock_id": "2330", "year_month": "2026-09", "revenue": "100", "note": "舊備註", "mom_growth": 1.0}]
    assert v.compare(source, db, ("stock_id", "year_month"), cols={"revenue"}) == ([], [], [])
    _, _, diffs = v.compare(source, db, ("stock_id", "year_month"))
    assert {d[1] for d in diffs} == {"note", "mom_growth"}


def test_same():
    assert v.same(1, "1.0") and v.same(None, None)
    assert not v.same(None, 0) and not v.same(1.0, 1.01)


def test_pick_days_override(monkeypatch):
    monkeypatch.setenv("VERIFY_DAYS", "2026-10-20, 2026-10-21")
    assert v.pick_days() == ["2026-10-20", "2026-10-21"]
