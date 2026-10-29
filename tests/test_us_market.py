"""美股收盤抓取：解析 Yahoo chart API 與美國財政部殖利率 CSV（不連網路）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fetch_us_market as us  # noqa: E402

# 美東時間（gmtoffset -14400）10/27、10/28 收盤；第三筆收盤是空值（還沒收盤或停牌）
YAHOO = {"chart": {"result": [{
    "meta": {"symbol": "TSM", "gmtoffset": -14400},
    "timestamp": [1761571800, 1761658200, 1761744600],
    "indicators": {"quote": [{
        "open": [430.0, 436.0, None], "high": [440.0, 441.5, None], "low": [428.0, 434.0, None],
        "close": [436.89, 439.51, None], "volume": [9000000, 11000000, None],
    }]},
}], "error": None}}

TREASURY = """﻿Date,"1 Mo","2 Yr","10 Yr","30 Yr"
10/28/2026,4.30,4.67,5.05,5.37
10/27/2026,4.31,4.70,4.99,5.30
"""


def test_parse_yahoo_dates_and_changes():
    rows = us.parse_yahoo(YAHOO, "TSM", "台積電 ADR", "adr")
    assert [r["date"] for r in rows] == ["2025-10-27", "2025-10-28"]
    first, last = rows
    assert first["change"] is None and first["prev_close"] is None
    assert last["prev_close"] == 436.89
    assert round(last["change"], 2) == 2.62
    assert round(last["change_pct"], 2) == 0.60
    assert last["volume"] == 11000000 and last["source"] == "yahoo" and last["category"] == "adr"


def test_parse_yahoo_empty_result():
    assert us.parse_yahoo({"chart": {"result": None, "error": {"code": "Not Found"}}}, "X", "X", "index") == []


def test_parse_treasury_sorted_with_changes():
    out = us.parse_treasury_csv(TREASURY)
    ten = out["UST10Y"]
    assert [r["date"] for r in ten] == ["2026-10-27", "2026-10-28"]
    assert ten[-1]["close"] == 5.05 and round(ten[-1]["change"], 2) == 0.06
    assert out["UST2Y"][-1]["close"] == 4.67 and out["UST30Y"][-1]["category"] == "bond"
    assert "1 Mo" not in {r["name"] for rows in out.values() for r in rows}


def test_collect_reports_missing_symbols(monkeypatch):
    monkeypatch.setattr(us, "SLEEP_BETWEEN_API", 0)
    monkeypatch.setattr(us, "SYMBOLS", [("TSM", "台積電 ADR", "adr"), ("BAD", "壞代號", "semi")])

    def fake_yahoo(symbol, name, category):
        if symbol == "BAD":
            raise ValueError("404")
        return us.parse_yahoo(YAHOO, symbol, name, category)

    monkeypatch.setattr(us, "fetch_yahoo", fake_yahoo)
    monkeypatch.setattr(us, "fetch_treasury", lambda: us.parse_treasury_csv(TREASURY))
    records, problems = us.collect()
    assert {r["symbol"] for r in records} == {"TSM", "UST2Y", "UST10Y", "UST30Y"}
    assert problems == ["BAD 壞代號：下載失敗（ValueError）"]
