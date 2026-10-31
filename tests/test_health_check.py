"""每週系統自我檢查：各來源停更、備份沒產生要報錯；資料庫快滿時自動縮短保留期限"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cleanup_old_data  # noqa: E402
import health_check  # noqa: E402
from fake_supabase import FakeClient  # noqa: E402

TODAY = date(2026, 10, 30)


def fresh_value(kind, col):
    if kind == "day":
        return {col: "2026-10-29"}
    if kind == "month":
        return {col: "2026-09"}
    if col == "period":
        return {col: "2026-Q2"}
    return {"year": 2026, "quarter": 2}


def clients(stale=None):
    """每張表一筆最新資料；stale[表] 換成指定的列"""
    tables = {"main": {}, "etf": {}}
    for db, table, col, kind, filt, _ in health_check.CHECKS:
        row = (stale or {}).get(table) or fresh_value(kind, col)
        if filt:
            row = {**row, filt[0]: filt[1]}
        tables[db][table] = [row]
    return {db: FakeClient(t) for db, t in tables.items()}


def test_period_end():
    assert health_check.period_end("day", "2026-10-29") == date(2026, 10, 29)
    assert health_check.period_end("month", "2026-09") == date(2026, 9, 30)
    assert health_check.period_end("month", "2026-12") == date(2026, 12, 31)
    assert health_check.period_end("quarter", "2026-Q3") == date(2026, 9, 30)
    assert health_check.period_end("quarter", (2025, 4)) == date(2025, 12, 31)


def test_all_fresh_passes():
    text, problems = health_check.check_freshness(clients(), TODAY)
    assert problems == []
    assert "daily_prices" in text and "us_market_daily" in text


def test_stale_source_is_reported():
    _, problems = health_check.check_freshness(clients({"futures_institutional": {"date": "2026-10-01"}}), TODAY)
    assert len(problems) == 1 and "futures_institutional" in problems[0] and "29 天" in problems[0]


def test_quarterly_uses_latest_quarter():
    # 同一年有好幾季時要取最新的一季，不是第一筆
    cs = clients()
    cs["main"].tables["quarterly_report"] = [{"stock_id": "2330", "year": 2025, "quarter": q} for q in (1, 2, 3, 4)]
    _, problems = health_check.check_freshness(cs, date(2026, 5, 1))
    assert not any("quarterly_report" in p for p in problems)


def test_empty_table_is_reported():
    cs = clients()
    cs["etf"].tables["etf_nav"] = []
    _, problems = health_check.check_freshness(cs, TODAY)
    assert problems == ["etf_nav：沒有任何資料"]


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def test_backup_age(monkeypatch):
    listing = [{"name": "archive"}, {"name": "2026-10-18"}, {"name": "2026-10-25"}]
    monkeypatch.setattr(health_check.requests, "post", lambda *a, **k: FakeResponse(listing))
    text, problems = health_check.check_backup("https://x.supabase.co", "k", TODAY)
    assert problems == [] and "2026-10-25" in text
    _, problems = health_check.check_backup("https://x.supabase.co", "k", date(2026, 11, 10))
    assert len(problems) == 1 and "16 天" in problems[0]


def test_retention_tiers():
    normal = cleanup_old_data.retention_months(400)
    assert normal == {"daily_prices": 36, "stock_ins_daily": 24, "margin_utilization": 12,
                      "holder_distribution": 24, "daily_signals": 12}
    assert cleanup_old_data.retention_months(None) == normal
    t1 = cleanup_old_data.retention_months(480)
    assert (t1["daily_prices"], t1["stock_ins_daily"], t1["margin_utilization"]) == (30, 18, 12)
    t2 = cleanup_old_data.retention_months(495)
    assert (t2["daily_prices"], t2["stock_ins_daily"], t2["holder_distribution"]) == (24, 12, 12)


def test_months_ago():
    assert cleanup_old_data.months_ago(date(2026, 10, 30), 36) == date(2023, 10, 30)
    assert cleanup_old_data.months_ago(date(2026, 8, 31), 30) == date(2024, 2, 29)
    assert cleanup_old_data.months_ago(date(2026, 3, 31), 1) == date(2026, 2, 28)


def test_shorter_retention_archives_then_deletes():
    rows = [{"stock_id": s, "date": d, "close_price": 1.0}
            for d in ("2024-03-01", "2024-06-01") for s in ("2330", "2317")]
    sb = FakeClient({"daily_prices": rows})
    # 30 個月：2024-04-30 以前要清掉，3 月那天先封存再刪除，6 月留著
    n = cleanup_old_data.cleanup(sb, "daily_prices", "date", 3, TODAY, dry_run=False, months=30)
    assert n == 2
    assert {r["date"] for r in sb.tables["daily_prices"]} == {"2024-06-01"}
    assert "daily_prices/2024/2024-03-01.csv.gz" in sb.storage.buckets["archive"]


def test_keepalive_skips_manual_and_deleted(monkeypatch):
    workflows = [{"id": 1, "name": "每日", "path": ".github/workflows/daily_update.yml", "state": "active"},
                 {"id": 2, "name": "閒置停用", "path": ".github/workflows/etf_update.yml", "state": "disabled_inactivity"},
                 {"id": 3, "name": "手動停用", "path": ".github/workflows/us_market.yml", "state": "disabled_manually"},
                 {"id": 4, "name": "已刪除", "path": ".github/workflows/probe_fee.yml", "state": "active"}]
    enabled = []

    class Put:
        status_code = 204
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    monkeypatch.setattr(health_check.requests, "get", lambda *a, **k: FakeResponse({"workflows": workflows}))
    monkeypatch.setattr(health_check.requests, "put", lambda url, **k: enabled.append(url.split("/")[-2]) or Put())
    text, problems = health_check.keep_workflows_enabled("t", "o/r")
    assert problems == [] and enabled == ["1", "2"] and "2 / 2" in text


# ==================== LINE 早報、資料庫用量 ====================
class FakeResponse:
    def __init__(self, payload=None, status=200, text=None):
        self.payload, self.status_code, self.text = payload, status, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise health_check.requests.HTTPError(str(self.status_code))

    def json(self):
        if self.payload is None:
            raise ValueError("not json")
        return self.payload


def test_brief_recent_is_ok(monkeypatch):
    monkeypatch.setattr(health_check.requests, "get",
                        lambda *a, **k: FakeResponse({"date": "2026-10-29", "images": []}))
    text, problems = health_check.check_brief("https://x.supabase.co", "k", TODAY)
    assert problems == [] and "2026-10-29" in text


def test_brief_stale_is_problem(monkeypatch):
    monkeypatch.setattr(health_check.requests, "get", lambda *a, **k: FakeResponse({"date": "2026-10-27"}))
    _, problems = health_check.check_brief("https://x.supabase.co", "k", TODAY)
    assert len(problems) == 1 and "3 天" in problems[0]


def test_brief_missing_is_problem(monkeypatch):
    monkeypatch.setattr(health_check.requests, "get", lambda *a, **k: FakeResponse(status=404))
    _, problems = health_check.check_brief("https://x.supabase.co", "k", TODAY)
    assert len(problems) == 1


def size_clients(main_mb, etf_mb):
    def make(mb):
        return FakeClient(rpcs={"database_size": lambda c: int(mb * 1024 * 1024)})
    return {"main": make(main_mb), "etf": make(etf_mb)}


def test_sizes_normal():
    text, problems = health_check.check_sizes(size_clients(346, 133))
    assert problems == [] and "346 MB" in text and "133 MB" in text


def test_sizes_etf_over_warning():
    _, problems = health_check.check_sizes(size_clients(346, 460))
    assert len(problems) == 1 and "ETF" in problems[0]


def test_sizes_main_not_warned_here():
    """主資料庫的門檻交給 check_data.py 和自動縮短保留期限，這裡只列出"""
    _, problems = health_check.check_sizes(size_clients(480, 133))
    assert problems == []
