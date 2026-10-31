"""證交所短時間請求太多會回錯誤頁（不是 JSON）：等一下再試一次，第二次還不行才報錯"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fetch_adjustments  # noqa: E402
import fetch_stock_info  # noqa: E402


class Resp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code, self.encoding = payload, status, None

    def raise_for_status(self):
        if self.status_code >= 400:
            raise fetch_stock_info.requests.HTTPError(f"{self.status_code} Client Error")

    def json(self):
        if self.payload is None:
            raise ValueError("Expecting value")
        return self.payload


def fake_get(payloads, calls):
    def get(*a, **k):
        calls.append(a[0])
        p = payloads[len(calls) - 1]
        return p if isinstance(p, Resp) else Resp(p)
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


@pytest.mark.parametrize("mod, fn", [(fetch_stock_info, "get_json"), (fetch_adjustments, "_get_json")])
def test_403_once_then_ok(monkeypatch, sleeps, mod, fn):
    """櫃買中心短時間請求太多會回 403：等一下再試一次"""
    calls = []
    monkeypatch.setattr(mod.requests, "get", fake_get([Resp(None, 403), {"tables": []}], calls))
    assert getattr(mod, fn)("https://www.tpex.org.tw/x") == {"tables": []}
    assert len(calls) == 2 and sleeps == [mod.BLOCKED_WAIT]


@pytest.mark.parametrize("mod, fn", [(fetch_stock_info, "get_json"), (fetch_adjustments, "_get_json")])
def test_403_twice_raises(monkeypatch, sleeps, mod, fn):
    calls = []
    monkeypatch.setattr(mod.requests, "get", fake_get([Resp(None, 403), Resp(None, 403)], calls))
    with pytest.raises(fetch_stock_info.requests.HTTPError):
        getattr(mod, fn)("https://www.tpex.org.tw/x")
    assert len(calls) == 2


def test_404_not_retried(monkeypatch, sleeps):
    """網址錯誤（404）不是暫時被擋，不用等"""
    calls = []
    monkeypatch.setattr(fetch_adjustments.requests, "get", fake_get([Resp(None, 404)], calls))
    with pytest.raises(fetch_stock_info.requests.HTTPError):
        fetch_adjustments._get_json("u")
    assert len(calls) == 1 and sleeps == []


def test_revenue_retries_when_blocked(monkeypatch, sleeps):
    calls = []
    rows = [{"公司代號": "2330", "資料年月": "11509", "營業收入-當月營收": "100"}]
    monkeypatch.setattr(fetch_stock_info.requests, "get", fake_get([Resp(None, 403), rows], calls))
    out = fetch_stock_info.fetch_revenue("u", "上櫃", {"2330"})
    assert len(out) == 1 and out[0]["year_month"] == "2026-09" and len(calls) == 2
