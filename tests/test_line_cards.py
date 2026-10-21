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


def test_rebuild_only_when_stale(use_db, monkeypatch):
    """早上補跑：卡片已是最新就不重算，舊了或沒有才重新產生"""
    sb = use_db()
    assert line_cards.is_stale(sb)          # 還沒有卡片
    line_cards.run(sb)
    assert not line_cards.is_stale(sb)
    bucket = sb.storage.from_(line_cards.BUCKET)
    path = f"{line_cards.PREFIX}/index.json"
    bucket.files[path] = json.dumps({"date": "2000-01-01", "stocks": {}}).encode()
    assert line_cards.is_stale(sb)
    monkeypatch.setenv("LINE_CARDS_IF_STALE", "1")
    import utils.db as db
    monkeypatch.setattr(db, "supabase", sb)
    assert line_cards.main() == 0
    assert not line_cards.is_stale(sb)
