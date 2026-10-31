"""LINE「早報」摘要卡：用 10/31 週報的資料格式，卡片大小與內容在 LINE 的限制內"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import line_brief  # noqa: E402


def sample():
    stock = {"id": "2330", "name": "台積電", "close": 2370, "chg_pct": -1.86,
             "signal": "跌破 2,380 前低、收 2,370。站回 2,470（20 日線）才轉強。"}
    return {
        "title": "台股週報 10/31（六）", "date_tw": "2026-10-31", "us_date": "2026-10-30", "tw_prev_date": "2026-10-30",
        "headline": "英特爾財測失利，費半重挫 7.2%。" * 3,
        "us_index": [{"name": "道瓊工業", "close": 51770.31, "chg_pct": -1.09}, {"name": "那斯達克", "close": 25437.0, "chg_pct": -2.86},
                     {"name": "費城半導體", "close": 10688.8, "chg_pct": -7.2}],
        "adr": [{"name": "台積電 TSM", "close": 406.13, "chg_pct": -5.95, "sub": "換算 2,648 元，溢價 11.7%"}],
        "tw_market": [{"name": "加權指數", "value": "45,211.83", "change": "−1,156.50", "dir": -1},
                      {"name": "外資買賣超（上市櫃個股合計）", "value": "−258,999 張", "dir": -1},
                      {"name": "台指期夜盤（10/30 夜間）", "value": "跌 867 點", "dir": -1}],
        "stocks": [stock, {**stock, "id": "6669", "name": "緯穎", "signal": "很長的訊號" * 30}],
        "views": ["多空", "穩健做法：下週先不接。"],
    }


def test_brief_card():
    out = line_brief.brief_messages(sample(), "https://claude.ai/artifact/x")
    assert out["date"] == "2026-10-31"
    msg = out["messages"][0]
    assert msg["type"] == "flex" and len(msg["altText"]) <= 400
    body = json.dumps(msg["contents"], ensure_ascii=False)
    assert len(body.encode()) <= 30_000
    for word in ["費城半導體", "台積電 ADR", "加權指數", "外資買賣超", "台指期夜盤", "緯穎 6669", "穩健做法"]:
        assert word in body
    assert "道瓊" not in body
    assert msg["contents"]["footer"]["contents"][0]["action"]["uri"] == "https://claude.ai/artifact/x"


def test_long_signal_trimmed_and_no_url():
    out = line_brief.brief_messages(sample())
    assert "footer" not in out["messages"][0]["contents"]
    assert line_brief._first_sentence("很長" * 50).endswith("…")
    assert len(line_brief._first_sentence("很長" * 50)) == line_brief.SIGNAL_MAX
