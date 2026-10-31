"""證交所短時間請求太多會回錯誤頁（不是 JSON）：等一下再試一次，第二次還不行才報錯"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fetch_adjustments  # noqa: E402
import fetch_stock_info  # noqa: E402


class Resp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        if self.payload is None:
            raise ValueError("Expecting value")
        return self.payload


def fake_get(payloads, calls):
    def get(*a, **k):
        calls.append(a[0])
        return Resp(payloads[len(calls) - 1])
    return get


@pytest.fixture
def sleeps(monkeypatch):
    waited = []
    monkeypatch.setattr(fetch_stock_info.time, "sleep", waited.append)
    return waited


@pytest.mark.parametrize("mod, fn", [(fetch_stock_info, "get_json"), (fetch_adjustments, "_get_json")])
def test_blocked_once_then_ok(monkeypatch, sleeps, mod, fn):
    calls = []
    monkeypatch.setattr(mod.requests, "get", fake_get([None, {"stat": "OK"}], calls))
    assert getattr(mod, fn)("https://www.twse.com.tw/x") == {"stat": "OK"}
    assert len(calls) == 2 and sleeps == [mod.BLOCKED_WAIT]


@pytest.mark.parametrize("mod, fn", [(fetch_stock_info, "get_json"), (fetch_adjustments, "_get_json")])
def test_blocked_twice_raises(monkeypatch, sleeps, mod, fn):
    calls = []
    monkeypatch.setattr(mod.requests, "get", fake_get([None, None], calls))
    with pytest.raises(ValueError):
        getattr(mod, fn)("https://www.twse.com.tw/x")
    assert len(calls) == 2


def test_json_first_time_no_wait(monkeypatch, sleeps):
    calls = []
    monkeypatch.setattr(fetch_stock_info.requests, "get", fake_get([{"a": 1}], calls))
    assert fetch_stock_info.get_json("u") == {"a": 1} and sleeps == []
