"""ETF 頁的計算：含息報酬、殖利率、填息、存股試算"""
import pandas as pd
import pytest

from utils.etf import fill_label, fill_status, simulate_savings, total_return, trailing_dividends, xirr


def flat_prices(start, end, price=10.0):
    days = pd.bdate_range(start, end)
    return pd.DataFrame({"date": days.strftime("%Y-%m-%d"), "close_price": price})


def test_total_return_adds_back_dividends():
    events = pd.DataFrame({"etf_id": ["A", "A"], "date": ["2026-05-01", "2025-01-01"], "factor": [0.9, 0.5]})
    r = total_return(pd.Series({"A": 9.0, "B": 12.0}), pd.Series({"A": 10.0, "B": 10.0}), events,
                     "2026-01-01", "2026-10-01")
    assert r["A"] == pytest.approx(0.0)   # 跌的 1 元剛好是配息，含息報酬 0%；期間外的事件不算
    assert r["B"] == pytest.approx(20.0)


def test_trailing_dividends_last_year_only():
    divs = pd.DataFrame({"etf_id": ["A", "A", "A", "A"],
                         "ex_date": ["2025-09-01", "2026-01-15", "2026-07-15", "2026-11-15"],
                         "cash_dividend": [9, 0.5, 0.7, 1.0]})
    assert trailing_dividends(divs, "2026-10-30")["A"] == pytest.approx(1.2)


def test_fill_status():
    divs = pd.DataFrame({"etf_id": ["A", "B"], "ex_date": ["2026-10-05", "2026-10-05"], "cash_dividend": [1, 1]})
    prices = pd.DataFrame({"etf_id": ["A"] * 4 + ["B"] * 3,
                           "date": ["2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"] * 1 + ["2026-10-02", "2026-10-05", "2026-10-06"],
                           "close_price": [20, 19, 19.5, 20.1, 20, 19, 18]})
    out = fill_status(divs, prices).set_index("etf_id")
    assert out.loc["A", "填息"] == True and out.loc["A", "填息天數"] == 3  # noqa: E712
    assert out.loc["B", "填息"] == False and out.loc["B", "最新收盤"] == 18  # noqa: E712
    assert fill_label(True, 3) == "✅ 已填息（第 3 天）" and fill_label(False, None) == "⏳ 未填息"
    assert fill_label(None, None) == "—"


def test_xirr_one_year():
    assert xirr([("2025-01-01", -100), ("2026-01-01", 110)]) == pytest.approx(10.0, abs=0.01)
    assert xirr([("2025-01-01", -100)]) is None


def test_monthly_savings_with_dividend_reinvested():
    prices = flat_prices("2026-01-01", "2026-03-31")
    divs = pd.DataFrame({"ex_date": ["2026-02-10"], "pay_date": ["2026-02-20"], "cash_dividend": [1.0]})
    detail, s = simulate_savings(prices, divs, None, "2026-01-01", "2026-03-31", monthly=1000, day=1,
                                 fee_rate=0, min_fee=0)
    # 1/1 是週四、2/1 週日順延到 2/2、3/2：每次 100 股；除息前持有 200 股 → 配 200 元，2/20 再買 20 股
    assert s["buys"] == 3 and s["invested"] == 3000
    assert s["dividends"] == pytest.approx(200) and s["units"] == 320
    assert s["value"] == pytest.approx(3200) and s["return_pct"] == pytest.approx(200 / 3000 * 100)
    row = detail.set_index("date")
    assert row.loc[pd.Timestamp("2026-02-19"), "receivable"] == pytest.approx(200)  # 已除息、還沒發放也算進總資產
    assert row.loc[pd.Timestamp("2026-02-19"), "value"] == pytest.approx(2200)


def test_savings_cash_dividend_kept_and_fees():
    prices = flat_prices("2026-01-01", "2026-03-31")
    divs = pd.DataFrame({"ex_date": ["2026-02-10"], "pay_date": [None], "cash_dividend": [1.0]})
    _, s = simulate_savings(prices, divs, None, "2026-01-01", "2026-03-31", monthly=1000, day=1, reinvest=False,
                            fee_rate=0.001425, min_fee=1)
    # 第一個月 1000 元只夠買 99 股（990 元＋手續費 1 元），零錢 9 元留到下個月，之後每月 100 股；
    # 除息前持有 199 股 → 配 199 元，領現金不再投入
    assert s["units"] == 299 and s["dividends"] == pytest.approx(199) and s["fees"] == pytest.approx(3)
    assert s["value"] == pytest.approx(3000 - 3 + 199)


def test_savings_split_and_lump_sum():
    prices = pd.DataFrame({"date": ["2026-03-02", "2026-03-03", "2026-03-04"], "close_price": [40.0, 40.0, 10.0]})
    events = pd.DataFrame({"date": ["2026-03-04", "2026-01-05"], "event_type": ["分割", "除息"], "factor": [0.25, 0.9]})
    _, s = simulate_savings(prices, None, events, "2026-03-01", "2026-03-04", lump=4000, fee_rate=0, min_fee=0)
    assert s["buys"] == 1 and s["units"] == 400 and s["value"] == pytest.approx(4000)   # 1 拆 4，股數 × 4、除息不動股數


def test_savings_no_prices():
    detail, s = simulate_savings(flat_prices("2026-01-01", "2026-01-31"), None, None, "2026-05-01", "2026-06-01",
                                 monthly=1000)
    assert detail.empty and s is None
