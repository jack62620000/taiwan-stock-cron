"""舊資料封存：清除前先存成壓縮檔，封存失敗就不刪除"""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import archive  # noqa: E402
import cleanup_old_data  # noqa: E402
from fake_supabase import FakeClient  # noqa: E402

OLD_DAYS = ["2023-10-02", "2023-10-03"]
NEW_DAY = "2026-10-19"


def db():
    rows = []
    for d in OLD_DAYS + [NEW_DAY]:
        for sid, close in [("2330", 550.0), ("0050", 120.5), ("6488", 300.0)]:
            rows.append({"stock_id": sid, "date": d, "close_price": close, "volume": 1000})
    return FakeClient({"daily_prices": rows})


def test_archive_then_delete():
    sb = db()
    n = cleanup_old_data.cleanup(sb, "daily_prices", "date", 3, date(2026, 10, 20), dry_run=False)
    assert n == 6
    assert {r["date"] for r in sb.tables["daily_prices"]} == {NEW_DAY}
    files = sb.storage.buckets["archive"]
    assert sorted(files) == [f"daily_prices/2023/{d}.csv.gz" for d in OLD_DAYS]


def test_load_archive_returns_original_rows():
    sb = db()
    cleanup_old_data.cleanup(sb, "daily_prices", "date", 3, date(2026, 10, 20), dry_run=False)
    df = archive.load_archive(sb, "daily_prices", "2023-10-01", "2023-10-31")
    assert len(df) == 6
    # 代號開頭的 0 要保留
    assert set(df["stock_id"]) == {"2330", "0050", "6488"}
    assert df.loc[df["stock_id"] == "0050", "close_price"].tolist() == [120.5, 120.5]
    # 日期範圍外的不讀
    assert archive.load_archive(sb, "daily_prices", "2023-10-03", "2023-10-03")["date"].tolist() == [OLD_DAYS[1]] * 3


def test_upload_failure_keeps_data(monkeypatch):
    sb = db()

    def broken_upload(*a, **k):
        raise RuntimeError("Storage 暫時無法使用")
    monkeypatch.setattr(archive, "archive_day", lambda *a: broken_upload())
    monkeypatch.setattr(cleanup_old_data, "archive_day", archive.archive_day)
    with pytest.raises(RuntimeError):
        cleanup_old_data.cleanup(sb, "daily_prices", "date", 3, date(2026, 10, 20), dry_run=False)
    assert len(sb.tables["daily_prices"]) == 9  # 一筆都沒刪


def test_archive_only_keeps_data():
    sb = db()
    n = cleanup_old_data.cleanup(sb, "daily_prices", "date", 3, date(2026, 10, 20),
                                 dry_run=False, archive_only=True)
    assert n == 6
    assert len(sb.tables["daily_prices"]) == 9
    assert len(sb.storage.buckets["archive"]) == 2


def test_dry_run_neither_archives_nor_deletes():
    sb = db()
    n = cleanup_old_data.cleanup(sb, "daily_prices", "date", 3, date(2026, 10, 20), dry_run=True)
    assert n == 6
    assert len(sb.tables["daily_prices"]) == 9
    assert sb.storage.buckets == {}
