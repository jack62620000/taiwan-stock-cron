"""
證交所、櫃買中心每日抓取：用 2026-10-19 實際抓到的回應（tests/samples/，只留幾檔股票）檢查解析結果。
不連網路。來源改版、欄位位置變了時，程式要拒絕寫入，而不是存進錯的數字。
"""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fetch_stock_info as f  # noqa: E402

SAMPLES = Path(__file__).resolve().parent / "samples"
DAY = "2026-10-19"
VALID = {"1101", "2317", "2330", "3105", "6488", "8069"}

# 網址裡的關鍵字 → 樣本檔
ROUTES = {
    "MI_INDEX": "twse_price", "afterTrading/otc": "tpex_price",
    "fund/T86": "twse_ins", "insti/dailyTrade": "tpex_ins",
    "MI_MARGN": "twse_margin", "margin/balance": "tpex_margin",
}


def sample(name):
    return json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def serve(monkeypatch):
    """讓 get_json 回傳樣本；可傳入 {樣本名: 改過的內容} 模擬改版"""
    def _serve(overrides=None):
        overrides = overrides or {}

        def fake_get_json(url, **_):
            name = next(v for k, v in ROUTES.items() if k in url)
            return overrides.get(name) or sample(name)
        monkeypatch.setattr(f, "get_json", fake_get_json)
    _serve()
    return _serve


def by_id(records):
    return {r["stock_id"]: r for r in records}


def test_twse_price(serve):
    r = by_id(f.fetch_twse_daily_all(DAY, VALID))
    assert set(r) == {"1101", "2317", "2330"}
    assert r["2330"] == {"stock_id": "2330", "date": DAY, "open_price": 2395.0, "high_price": 2420.0,
                         "low_price": 2390.0, "close_price": 2420.0, "volume": 27694594}


def test_tpex_price(serve):
    r = by_id(f.fetch_tpex_otc_daily(DAY, VALID))
    assert r["6488"] == {"stock_id": "6488", "date": DAY, "open_price": 865.0, "high_price": 883.0,
                         "low_price": 838.0, "close_price": 882.0, "volume": 11582000}


def test_twse_ins(serve):
    r = by_id(f.fetch_twse_stock_ins(DAY, VALID))
    # 外資 = 外陸資（不含外資自營商）＋外資自營商
    assert r["2330"]["foreign_net_shares"] == -9834711
    assert r["2330"]["sitca_net_shares"] == 3884574
    assert r["2330"]["dealers_net_shares"] == 1346165


def test_tpex_ins(serve):
    r = by_id(f.fetch_tpex_stock_ins(DAY, VALID))
    assert r["3105"]["foreign_net_shares"] == 4080540
    assert r["3105"]["sitca_net_shares"] == -4118516
    assert r["3105"]["dealers_net_shares"] == 6380


def test_twse_margin(serve):
    r = by_id(f.fetch_twse_margin(DAY, VALID))
    assert r["2317"]["margin_balance"] == 60082
    assert r["2317"]["margin_limit"] == 3518936
    assert r["2317"]["margin_rate"] == round(60082 / 3518936 * 100, 2)
    assert r["2317"]["short_balance"] == 377


def test_tpex_margin(serve):
    r = by_id(f.fetch_tpex_margin(DAY, VALID))
    assert r["3105"] == {"stock_id": "3105", "date": DAY, "margin_balance": 43750, "margin_limit": 105985,
                         "margin_rate": 41.27, "short_balance": 752, "short_rate": 0.7}


def test_only_valid_stocks(serve):
    assert [r["stock_id"] for r in f.fetch_twse_daily_all(DAY, {"2330"})] == ["2330"]


def test_tpex_wrong_date_is_skipped(serve):
    """櫃買中心看不懂日期時會回傳最新一天，日期不符就不寫入"""
    data = sample("tpex_price")
    data["date"] = "20261016"
    serve({"tpex_price": data})
    assert f.fetch_tpex_otc_daily(DAY, VALID) == []


def test_twse_not_ready(serve):
    serve({"twse_ins": {"stat": "很抱歉，沒有符合條件的資料!"}})
    assert f.fetch_twse_stock_ins(DAY, VALID) == []


@pytest.mark.parametrize("name, fetch, path", [
    ("twse_price", f.fetch_twse_daily_all, ("tables", 1)),
    ("tpex_price", f.fetch_tpex_otc_daily, ("tables", 0)),
    ("twse_ins", f.fetch_twse_stock_ins, ()),
    ("tpex_ins", f.fetch_tpex_stock_ins, ("tables", 0)),
    ("twse_margin", f.fetch_twse_margin, ("tables", 1)),
    ("tpex_margin", f.fetch_tpex_margin, ("tables", 0)),
])
def test_column_shift_is_rejected(serve, name, fetch, path):
    """來源在前面插入一欄（欄位位置全部往後移）時，不能把錯位的數字寫進去"""
    data = copy.deepcopy(sample(name))
    node = data
    for key in path:
        node = node[key]
    node["fields"].insert(2, "新欄位")
    for row in node["data"]:
        row.insert(2, "0")
    serve({name: data})
    assert fetch(DAY, VALID) == []
