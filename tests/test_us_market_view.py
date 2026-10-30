"""首頁「昨晚美股」的計算：最新一筆、台積電 ADR 溢價、費半和隔天加權的對照"""
import pandas as pd
import pytest

from utils.us_market import adr_premium, latest_rows, sox_vs_taiex


def us_rows(rows):
    return pd.DataFrame(rows, columns=["symbol", "date", "name", "category", "close", "change", "change_pct"])


def test_latest_rows_one_per_symbol():
    us = us_rows([("^SOX", "2026-10-27", "費半", "index", 100, None, None),
                  ("^SOX", "2026-10-28", "費半", "index", 102, 2, 2.0),
                  ("TSM", "2026-10-28", "台積電 ADR", "adr", 300, 3, 1.0)])
    latest = latest_rows(us).set_index("symbol")
    assert latest.loc["^SOX", "close"] == 102 and len(latest) == 2
    assert latest_rows(None).empty


def test_adr_premium_uses_tw_close_on_or_before_adr_date():
    us = us_rows([("TSM", "2026-10-28", "台積電 ADR", "adr", 300.0, 0, 0),
                  ("TWD=X", "2026-10-28", "美元／台幣", "fx", 32.0, 0, 0),
                  ("TWD=X", "2026-10-29", "美元／台幣", "fx", 99.0, 0, 0)])   # ADR 之後的匯率不能用
    tw = pd.DataFrame({"date": ["2026-10-27", "2026-10-28", "2026-10-29"], "close_price": ["1800", "1900", "2500"]})
    p = adr_premium(us, tw)
    assert p["implied"] == pytest.approx(300 * 32 / 5)
    assert p["tw_date"] == "2026-10-28" and p["tw_close"] == 1900
    assert p["premium_pct"] == pytest.approx((1920 / 1900 - 1) * 100)


def test_adr_premium_missing_data():
    us = us_rows([("TSM", "2026-10-28", "台積電 ADR", "adr", 300.0, 0, 0)])
    assert adr_premium(us, pd.DataFrame({"date": ["2026-10-28"], "close_price": [1900]})) is None
    assert adr_premium(us, pd.DataFrame()) is None


def test_sox_vs_taiex_pairs_next_trading_day():
    us = us_rows([("^SOX", "2026-10-23", "費半", "index", 100, None, None),   # 沒有漲跌幅，略過
                  ("^SOX", "2026-10-24", "費半", "index", 101, 1, 1.0),     # 週五 → 對台股週一
                  ("^SOX", "2026-10-27", "費半", "index", 99, -2, -2.0),    # 週一 → 對台股週二
                  ("^SOX", "2026-10-28", "費半", "index", 100, 1, 1.0)])    # 台股還沒開盤，略過
    taiex = pd.DataFrame({"date": ["2026-10-24", "2026-10-27", "2026-10-28"], "close": [100, 102, 101]})
    link = sox_vs_taiex(us, taiex)
    assert list(link["美股日期"]) == ["2026-10-24", "2026-10-27"]
    assert list(link["台股日期"]) == ["2026-10-27", "2026-10-28"]
    assert link["加權%"].tolist() == pytest.approx([2.0, (101 / 102 - 1) * 100])
    assert sox_vs_taiex(None, taiex).empty
