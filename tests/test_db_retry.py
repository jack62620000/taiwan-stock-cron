"""連線被中途關掉時，查詢會自動重試"""
import httpx

import utils.db as db


class FlakyQuery:
    def __init__(self, failures):
        self.failures = failures
        self.calls = 0

    def execute(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise httpx.RemoteProtocolError("Server disconnected")
        return "ok"


def test_retry_after_disconnect(monkeypatch):
    monkeypatch.setattr(db.time, "sleep", lambda s: None)
    q = FlakyQuery(failures=1)
    assert db._execute(q) == "ok"
    assert q.calls == 2


def test_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr(db.time, "sleep", lambda s: None)
    q = FlakyQuery(failures=5)
    try:
        db._execute(q)
    except httpx.RemoteProtocolError:
        pass
    else:
        raise AssertionError("應該要放棄並丟出錯誤")
    assert q.calls == 3
