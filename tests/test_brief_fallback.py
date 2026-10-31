"""早報備援：完整早報沒產生時，用資料庫數字組一份簡易版 data.json"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "brief"))

import brief_fallback as bf  # noqa: E402
from fake_supabase import FakeClient  # noqa: E402

TW_DATE = "2026-10-30"


def us_rows():
    rows = [("^DJI", "道瓊工業指數", "index", 51770.31, -560.0, -1.09), ("^GSPC", "S&P 500", "index", 6800.5, -40, -0.58),
            ("^SOX", "費城半導體", "index", 7000, -545, -7.2), ("TSM", "台積電 ADR", "adr", 406.13, -25.7, -5.95),
            ("UMC", "聯電 ADR", "adr", 8.1, 0.1, 1.2), ("NVDA", "輝達", "mag7", 200, 2, 1.0),
            ("INTC", "英特爾", "semi", 65.76, -26.3, -28.58), ("UST10Y", "美國 10 年期公債殖利率", "bond", 4.21, -0.05, -1.1),
            ("CL=F", "WTI 原油", "commodity", 61.2, 0.5, 0.82), ("AAPL", "蘋果", "mag7", None, None, None)]
    return [{"symbol": s, "date": "2026-10-30", "name": n, "category": c, "close": cl, "change": ch, "change_pct": p}
            for s, n, c, cl, ch, p in rows]


def main_tables():
    days = [(date(2026, 8, 1) + timedelta(days=i)).isoformat() for i in range(91)]
    prices = [{"stock_id": "2330", "date": d, "close_price": 2000 + i * 5} for i, d in enumerate(days)]
    return {
        "market_index": [{"index_code": "TAIEX", "date": TW_DATE, "close": 45211.83, "change": -1156.5, "trade_value": 6.5e11},
                         {"index_code": "TPEX", "date": TW_DATE, "close": 280.1, "change": 2.2, "trade_value": 1e11}],
        "futures_institutional": [{"date": TW_DATE, "item": "外資及陸資", "net_oi": -92241},
                                  {"date": TW_DATE, "item": "投信", "net_oi": 89211}],
        "stock_ins_daily": [{"stock_id": sid, "date": TW_DATE, "foreign_net_shares": n, "sitca_net_shares": 1000,
                             "dealers_net_shares": -500, "foreign_hold_ratio": 70.5} for sid, n in (("2330", -26129000), ("2317", 129000))],
        "watch_groups": [{"id": 1, "owner": "me"}, {"id": 5, "owner": "other"}],
        "watchlist": [{"stock_id": "2330", "group_id": 1, "added_at": "2026-01-01"},
                      {"stock_id": "9999", "group_id": 5, "added_at": "2026-01-01"}],
        "stock_info": [{"stock_id": "2330", "stock_name": "台積電", "industry": "半導體業"}],
        "daily_prices": prices,
        "daily_signals": [{"stock_id": "2330", "date": TW_DATE, "total_score": 37.0, "rank": 288}],
    }


def test_build_data_has_every_section():
    d = bf.build_data(FakeClient(main_tables()), FakeClient({"us_market_daily": us_rows()}), "2026-10-31", owner="me")
    assert d["date_tw"] == "2026-10-31" and d["tw_prev_date"] == TW_DATE and "簡易版" in d["title"]
    assert [q["name"] for q in d["us_index"]] == ["道瓊工業指數", "S&P 500", "費城半導體"]
    assert d["adr"][0]["name"] == "台積電 ADR"          # 漲跌幅大的排前面
    assert [q["name"] for q in d["mag7"]] == ["輝達"]    # 沒有收盤價的略過
    assert d["rates"][0] == {"name": "美債 10 年", "value": "4.21%", "change": "−5 bp", "dir": -1}
    tw = {r["name"]: r for r in d["tw_market"]}
    assert tw["加權指數"]["value"] == "45,211.83" and tw["加權指數"]["dir"] == -1
    assert tw["外資買賣超"]["value"] == "−26,000 張" and tw["外資台指期淨部位"]["value"] == "−92,241 口"
    assert [s["id"] for s in d["stocks"]] == ["2330"]    # 只用 owner 的自選股
    s = d["stocks"][0]
    assert s["close"] == 2450 and s["ret5"] > 0 and len(s["spark"]["closes"]) == 20 and s["score_text"] == "37 分／第 288 名"
    assert "S&P 500 −0.58%" in d["headline"] and "45,211.83" in d["headline"]


def test_layout_renders_without_news_and_calendar():
    import full_layouts
    d = bf.build_data(FakeClient(main_tables()), FakeClient({"us_market_daily": us_rows()}), "2026-10-31")
    html = full_layouts.listing_cards(d)
    assert "台積電 ADR" in html and "台積電" in html
    assert ">新聞<" not in html and ">行事曆<" not in html and ">昨日點名回顧<" not in html


def test_stock_card_short_history():
    prices = [{"close_price": 100}, {"close_price": 102}]
    s = bf.stock_card("1234", "測試", None, prices, [], None)
    assert s["chg_pct"] == 2.0 and s["ret5"] is None and s["ma_flags"] == [] and s["f5_text"] == "—"


def test_skip_when_today_brief_exists(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "k")
    monkeypatch.setattr(bf, "current_brief_date", lambda url, key: (bf.datetime.now(bf.timezone.utc) + bf.timedelta(hours=8)).strftime("%Y-%m-%d"))
    monkeypatch.setattr(bf, "create_client", lambda *a: (_ for _ in ()).throw(AssertionError("不該連資料庫")))
    assert bf.main() == 0
