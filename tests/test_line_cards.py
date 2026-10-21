"""LINE 查詢卡片：整批算出的卡片要和逐檔算（每日推播、個股分析卡頁）的完全相同"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import line_cards  # noqa: E402
import line_push  # noqa: E402


def stored(sb, name):
    return json.loads(sb.storage.from_(line_cards.BUCKET).download(f"{line_cards.PREFIX}/{name}"))


def test_cards_match_single_stock(use_db):
    sb = use_db()
    n = line_cards.run(sb)
    index = stored(sb, "index.json")
    assert n == len(index["stocks"]) > 0
    assert index["date"] == sb.tables["daily_signals"][-1]["date"] or index["date"]
    data = line_cards.load_all()
    for sid in index["stocks"]:
        card = stored(sb, f"{line_cards.shard_of(sid)}.json")[sid]
        expected = line_push.stock_bubble(sid, line_push.analyze_stock(sid, data["momentum"].get(sid)))
        assert card == json.loads(json.dumps(expected, ensure_ascii=False)), sid


def test_dry_run_does_not_upload(use_db, capsys):
    sb = use_db()
    assert line_cards.run(sb, dry_run=True) > 0
    assert not sb.storage.from_(line_cards.BUCKET).files
    assert "未上傳" in capsys.readouterr().out


def test_no_signals_skips(use_db):
    sb = use_db(empty=True)
    assert line_cards.run(sb) == 0
