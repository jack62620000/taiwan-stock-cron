"""每日資料檢查：筆數少於平常九成要報錯（用假資料庫）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_data  # noqa: E402
from fake_supabase import FakeClient  # noqa: E402

DAYS = ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19"]


def db(counts=None):
    """每張表每天 100 檔；counts[(表, 日期)] 可以改成別的筆數"""
    counts = counts or {}
    tables = {}
    for table, _ in check_data.TABLES:
        rows = []
        for d in DAYS:
            n = counts.get((table, d), 100)
            rows += [{"stock_id": "2330" if i == 0 else f"9{i:03d}", "date": d} for i in range(n)]
        tables[table] = rows
    return FakeClient(tables)


def test_normal_days_pass():
    text, problems = check_data.check(db())
    assert problems == []
    assert "2026-10-19" in text


def test_short_day_is_reported():
    _, problems = check_data.check(db({("stock_ins_daily", "2026-10-19"): 50}))
    assert problems == ["2026-10-19 三大法人（stock_ins_daily）只有 50 筆，平常約 100 筆"]


def test_older_days_not_rechecked():
    # 超過最近 3 天的缺漏之前就檢查過了，不再重複報錯
    _, problems = check_data.check(db({("margin_utilization", "2026-10-12"): 10}))
    assert problems == []
