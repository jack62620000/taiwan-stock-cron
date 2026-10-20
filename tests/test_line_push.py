"""LINE 推播：用和自選股頁相同的分析組訊息，同一個評分日只推一次"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import line_push  # noqa: E402


class FakePost:
    def __init__(self, status=200):
        self.calls, self.status = [], status

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append(json)
        return type("Res", (), {"status_code": self.status, "text": "error"})()


def all_messages(post):
    return [m for c in post.calls for m in c["messages"]]


def test_push_all_groups(use_db):
    sb = use_db()
    sb.tables["watch_groups"].append({"id": 2, "owner": "tester", "name": "空分組"})
    post = FakePost()
    n = line_push.run(sb, "token", "U123", post=post)
    ids = [r["stock_id"] for r in sb.tables["watchlist"]]
    assert n == len(ids)
    msgs = all_messages(post)
    assert msgs[0]["type"] == "text" and "我的自選股" in msgs[0]["text"] and "空分組" not in msgs[0]["text"]
    bubbles = [b for m in msgs[1:] for b in m["contents"]["contents"]]
    assert len(bubbles) == len(ids)
    text = json.dumps(bubbles, ensure_ascii=False)
    assert "葛蘭碧" in text and "分" in text
    assert all(c["to"] == "U123" and len(c["messages"]) <= 5 for c in post.calls)


def test_same_day_pushed_once(use_db):
    sb = use_db()
    post = FakePost()
    assert line_push.run(sb, "token", "U123", post=post) > 0
    assert line_push.run(sb, "token", "U123", post=post) == 0
    assert len(post.calls) == 1
    assert line_push.run(sb, "token", "U123", force=True, post=post) > 0


def test_failed_push_not_marked(use_db):
    sb = use_db()
    try:
        line_push.run(sb, "token", "U123", post=FakePost(status=401))
    except RuntimeError:
        pass
    else:
        raise AssertionError("推播失敗應該丟出錯誤")
    assert line_push.last_pushed(sb) is None


def test_carousel_split_at_12():
    ids = [str(1000 + k) for k in range(15)]
    import pandas as pd
    msgs = line_push.build_messages([("大分組", ids)], {}, "2026-10-20", pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
    assert [len(m["contents"]["contents"]) for m in msgs[1:]] == [12, 3]


def test_no_secrets_skips(monkeypatch):
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("LINE_USER_ID", raising=False)
    assert line_push.main() == 0


def test_dry_run_does_not_push(use_db, capsys):
    sb = use_db()
    post = FakePost()
    assert line_push.run(sb, None, None, post=post, dry_run=True) == 0
    assert post.calls == [] and line_push.last_pushed(sb) is None
    assert "自選股分析" in capsys.readouterr().out
