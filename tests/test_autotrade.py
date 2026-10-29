"""照規則自動模擬：每天照規則買賣（utils.autotrade.run_rule）與每晚的排程（auto_paper.py）"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.autotrade import run_rule

D = [f"2026-11-{d:02d}" for d in range(2, 30)]   # 假交易日


def prices(ids, value=100.0):
    return {(s, d): value for s in ids for d in D}


def test_buys_top_ranks_next_open_and_sells_after_hold():
    ranks = {d: ["A", "B", "C"] for d in D}
    ok = {d: True for d in D}
    sells, buys, done = run_rule([], D, D[0], ranks, ok, prices("ABC"), prices("ABC"), hold=3, slots=2)
    # D[1] 開盤用 D[0] 的排名買前 2 名，各用一半資金；第 3 個交易日（D[4]）開盤賣，同一天再買回
    first = [b for b in buys if b["buy_date"] == D[1]]
    assert [b["stock_id"] for b in first] == ["A", "B"]
    assert first[0]["sell_date"] == D[4] and first[0]["note"] == f"{D[0]} 評分第 1 名"
    assert first[0]["shares"] * 1000 == 998   # 100,000 ÷ (100 × 1.001425) 取整數股
    assert done == D[-1] and sells == []


def test_no_buy_when_market_not_ok_and_stops_when_data_missing():
    ranks = {d: ["A"] for d in D[:5]}
    ok = {d: False for d in D}
    sells, buys, done = run_rule([], D, D[0], ranks, ok, prices("A"), prices("A"))
    assert buys == [] and done == D[5]   # D[5] 用 D[4] 的排名；D[6] 要用的 D[5] 排名還沒有，停下


def test_continues_from_existing_trades_and_sells_due():
    trades = [{"id": 7, "stock_id": "A", "buy_date": D[1], "buy_price": 100.0, "shares": 1.0,
               "sell_date": None, "sell_price": None}]
    ranks = {d: ["A", "B"] for d in D}
    ok = {d: True for d in D}
    opens = {**prices("AB"), ("A", D[11]): 110.0}
    sells, buys, done = run_rule(trades, D, D[0], ranks, ok, opens, prices("AB"), hold=10, slots=2)
    assert sells[0] == (7, D[11], 110.0)
    assert buys[0]["stock_id"] == "B" and buys[0]["buy_date"] == D[2]   # 從最後一筆買進日之後接著做


def test_suspended_stock_waits_for_open_price():
    trades = [{"id": 1, "stock_id": "A", "buy_date": D[0], "buy_price": 100.0, "shares": 1.0,
               "sell_date": None, "sell_price": None}]
    opens = {**prices("AB"), ("A", D[2]): None}   # 當天其他股票有開盤價，只有 A 停牌
    sells, _, _ = run_rule(trades, D[:5], D[0], {d: [] for d in D}, {d: True for d in D}, opens, prices("A"), hold=2)
    assert sells == [(1, D[3], 100.0)]


def test_auto_paper_script_records_trades(use_db, monkeypatch):
    import auto_paper
    from utils.db import get_paper_trades
    use_db()
    monkeypatch.setenv("AUTO_PAPER_GROUP_ID", "11")
    # 假資料最近大盤都在均線下，這裡改成每天都可以買
    real = auto_paper.market_ok_by_date
    monkeypatch.setattr(auto_paper, "market_ok_by_date", lambda df: {d: True for d in real(df)})
    before = get_paper_trades(11)
    assert auto_paper.main() == 0
    after = get_paper_trades(11)
    assert len(after) > len(before)
    # 再跑一次不會重複記錄
    assert auto_paper.main() == 0
    assert len(get_paper_trades(11)) == len(after)


def test_auto_paper_without_group(use_db, monkeypatch):
    import auto_paper
    use_db(empty=True)
    monkeypatch.delenv("AUTO_PAPER_GROUP_ID", raising=False)
    assert auto_paper.main() == 0
    monkeypatch.setenv("AUTO_PAPER_GROUP_ID", "99")
    assert auto_paper.main() == 1


def test_equity_curve_matches_pnl():
    """總資產走勢最後一天 = 起始資金 + 全部損益（evaluate_trades）"""
    import pandas as pd
    from utils.paper import equity_curve, evaluate_trades
    trades = pd.DataFrame([
        {"stock_id": "A", "buy_date": D[1], "buy_price": 100.0, "shares": 1.0, "sell_date": D[3], "sell_price": 110.0},
        {"stock_id": "B", "buy_date": D[2], "buy_price": 50.0, "shares": 2.0, "sell_date": None, "sell_price": None},
    ])
    raw = pd.DataFrame([{"stock_id": s, "date": d, "close_price": p} for d in D[:6] for s, p in (("A", 105.0), ("B", 55.0))])
    result = evaluate_trades(trades, raw, raw)
    curve = equity_curve(result, raw, raw, 200_000)
    assert list(curve["date"]) == D[1:6]
    assert curve["equity"].iloc[-1] == pytest.approx(200_000 + result["pnl"].sum())
