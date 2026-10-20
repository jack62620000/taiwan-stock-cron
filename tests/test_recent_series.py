"""近期全市場資料：資料庫函式（一檔一列、陣列）展開後，要和逐日查詢的結果完全一樣"""
import pandas as pd

import utils.db as db


def same(a, b):
    key = ["date", "stock_id"]
    a = a.sort_values(key, ignore_index=True)
    b = b.sort_values(key, ignore_index=True)[list(a.columns)]
    pd.testing.assert_frame_equal(a.astype(str), b.astype(str))


def test_prices_match_daily_queries(use_db):
    use_db()
    latest = db.get_latest_date("daily_prices")
    cols = "stock_id, date, close_price, volume"
    same(db._recent_series("daily_prices", 40, cols), db._recent_snapshots("daily_prices", latest, 40, cols))


def test_ins_match_daily_queries(use_db):
    use_db()
    latest = db.get_latest_date("stock_ins_daily")
    cols = "stock_id, date, foreign_net_shares, sitca_net_shares"
    same(db._recent_series("stock_ins_daily", 10, cols), db._recent_snapshots("stock_ins_daily", latest, 10, cols))


def test_falls_back_when_function_fails(use_db):
    client = use_db()

    def broken(*a, **k):
        raise RuntimeError("函式不存在")
    client.rpc = broken
    df = db.get_recent_closes(calendar_days=40)
    assert not df.empty
    assert list(df.columns) == ["stock_id", "date", "close_price"]


def test_other_columns_use_daily_queries(use_db):
    use_db()
    df = db.get_recent_ins(calendar_days=10, columns="stock_id, date, dealers_net_shares")
    assert "dealers_net_shares" in df.columns
