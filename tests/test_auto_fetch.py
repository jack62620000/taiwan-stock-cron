"""每日排程：缺哪天補哪天（不連網路，用假資料庫和假的抓取函式）"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auto_fetch  # noqa: E402
from fake_supabase import FakeClient  # noqa: E402

TABLES = ("daily_prices", "stock_ins_daily", "margin_utilization")


def db_with(days, skip=()):
    """days 裡每天兩檔代表股三張表都有資料；skip 裡的 (表, 股票, 日期) 故意缺"""
    tables = {t: [] for t in TABLES}
    for t in TABLES:
        for d in days:
            for sid in auto_fetch.ANCHOR_STOCKS:
                if (t, sid, d) not in skip:
                    tables[t].append({"stock_id": sid, "date": d})
    return FakeClient(tables)


def test_missing_dates_finds_gaps(monkeypatch):
    # 2026-10-20 是週二：最近 7 天的平日是 10/14～10/16、10/19、10/20
    client = db_with(["2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19"],
                     skip={("margin_utilization", "6488", "2026-10-15")})
    monkeypatch.setattr(auto_fetch.f, "supabase", client)
    assert auto_fetch.missing_dates(date(2026, 10, 20)) == ["2026-10-15", "2026-10-20"]


def test_missing_dates_skips_weekend(monkeypatch):
    monkeypatch.setattr(auto_fetch.f, "supabase", db_with([]))
    # 週日：最近 7 天只有週一到週五
    assert auto_fetch.missing_dates(date(2026, 10, 18)) == [
        "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]


def test_target_date_override(monkeypatch):
    monkeypatch.setenv("TARGET_DATE", "2026-10-16")
    assert auto_fetch.target_dates() == ["2026-10-16"]


def test_main_fetches_each_missing_day(monkeypatch, tmp_path):
    """休市（或還沒公布）的日子不算錯誤；有行情的日子寫入，並把第一天交給評分步驟"""
    monkeypatch.delenv("TARGET_DATE", raising=False)
    monkeypatch.setattr(auto_fetch, "target_dates", lambda: ["2026-10-16", "2026-10-19", "2026-10-20"])
    monkeypatch.setattr(auto_fetch, "taiwan_today", lambda: date(2026, 10, 20))
    monkeypatch.setattr(auto_fetch.time, "sleep", lambda s: None)
    f = auto_fetch.f
    monkeypatch.setattr(f, "refresh_stock_info", lambda: None)
    monkeypatch.setattr(f, "get_valid_stock_ids", lambda: {"2330", "6488"})
    traded = {"2026-10-16", "2026-10-19"}
    row = lambda d: [{"stock_id": "2330", "date": d}]  # noqa: E731
    for name in ("fetch_twse_daily_all", "fetch_tpex_otc_daily", "fetch_twse_stock_ins",
                 "fetch_tpex_stock_ins", "fetch_twse_margin", "fetch_tpex_margin"):
        monkeypatch.setattr(f, name, lambda d, s: row(d) if d in traded else [])
    saved = []
    monkeypatch.setattr(f, "save_data_batches", lambda t, r: (saved.append((t, len(r))), (len(r), 0))[1])
    monkeypatch.setattr(auto_fetch.foreign, "fetch_day", lambda d, s: ({}, []))
    monkeypatch.setattr(auto_fetch.adj, "fetch_range", lambda a, b, s: ([], []))
    monkeypatch.setattr(auto_fetch.market, "fetch_range", lambda a, b: (None, []))
    monkeypatch.setattr(auto_fetch.revenue, "fetch_range", lambda a, b, s: (None, []))
    monkeypatch.setattr(f, "fetch_monthly_revenue", lambda s: [{"stock_id": "2330", "year_month": "2026-09"}])
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))

    assert auto_fetch.main() == 0
    assert saved.count(("daily_prices", 2)) == 2  # 10/16、10/19 各寫一次；10/20 沒行情不寫
    assert out.read_text().splitlines() == ["fetched=2", "start=2026-10-16"]
    assert ("monthly_revenue", 1) in saved  # 每天用開放資料覆蓋最新一個月，跟上公司更正
