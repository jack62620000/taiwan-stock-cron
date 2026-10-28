"""K線看盤：資料整理成圖表格式、頁面有畫出圖表"""
import json

import pandas as pd


def _payload_from_html(html):
    return json.loads(html.split("const RAW = ")[1].split(";\nconst LWC")[0].replace("<\\/", "</"))


def test_build_payload_keeps_order_and_events():
    from utils.kline import build_payload, build_html
    stock_list = pd.DataFrame({"stock_id": ["2330", "6669"], "stock_name": ["台積電", None], "industry": ["半導體業", None]})
    prices = pd.DataFrame({
        "stock_id": ["6669", "6669", "2330", "2330", "2330"],
        "date": ["2026-09-01", "2026-09-02", "2026-09-01", "2026-09-02", "2026-09-03"],
        "open_price": [7800, 2600, 1300, 1310, 0], "high_price": [7900, 2650, 1320, 1330, 0],
        "low_price": [7700, 2580, 1290, 1300, 0], "close_price": [7800, 2610, 1310, 1320, 0],
        "volume": [1_000_000, 3_000_000, 20_000_000, 25_000_000, 0],
    })
    events = pd.DataFrame({"stock_id": ["6669"], "date": ["2026-09-02"], "event_type": ["除權息"], "factor": [0.335]})
    taiex = pd.DataFrame({"date": ["2026-09-01"], "open": [20000], "high": [20100], "low": [19900], "close": [20050],
                          "trade_value": [450_000_000_000]})
    p = build_payload(["6669", "2330"], stock_list, prices, events, {"TAIEX": taiex}, "2026-09-03")
    assert p["order"] == ["TAIEX", "6669", "2330"] and p["first"] == "6669"
    assert p["meta"]["6669"]["name"] == "6669"            # 沒有名稱時用代號
    assert p["s"]["2330"]["d"] == [260901, 260902]         # 沒成交（價格 0）的日子跳過
    assert p["s"]["2330"]["v"] == [20000, 25000]           # 股 → 張
    assert p["s"]["TAIEX"]["v"] == [4500]                  # 元 → 億元
    assert p["adj"]["6669"] == [[260902, 0.335, "除權息"]]
    assert _payload_from_html(build_html(p)) == p


def test_build_payload_without_data():
    from utils.kline import build_payload
    assert build_payload(["2330"], pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {}, None) is None


def test_kline_page_draws_watchlist(use_db):
    from conftest import APP
    from test_pages import run_page
    import utils.db as db
    use_db()
    watch = list(db.get_watchlist(1)["stock_id"])
    at = run_page(APP / "pages" / "11_K線看盤.py")
    assert [e.value for e in at.exception] == []
    html = " ".join(str(getattr(e, "proto", "")) for e in at.main)
    assert "lightweight-charts" in html
    assert all(f"\\\"{sid}\\\"" in html or f'"{sid}"' in html for sid in watch)
