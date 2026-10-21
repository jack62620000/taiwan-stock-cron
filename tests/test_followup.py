"""訊號事後追蹤：法則 2 買點、隔天開盤買進的報酬、動能訊號紀錄的保留"""
import numpy as np
import pandas as pd

from fake_supabase import FakeClient


def _prices(rows):
    return pd.DataFrame(rows, columns=["stock_id", "date", "open_price", "close_price"])


def test_signal_followup_buys_next_open():
    from utils.analysis import followup_summary, signal_followup
    events = pd.DataFrame([
        {"stock_id": "A", "signal_date": "2026-10-01", "market": "上市"},
        {"stock_id": "B", "signal_date": "2026-10-01", "market": "上櫃"},
        {"stock_id": "A", "signal_date": "2026-10-03"},  # 今天剛出現，還沒買進
    ])
    prices = _prices([
        ("A", "2026-10-01", 9, 10), ("A", "2026-10-02", 10, 11), ("A", "2026-10-03", 11, 9),
        ("B", "2026-10-01", 50, 50), ("B", "2026-10-02", 0, 40), ("B", "2026-10-03", 41, 44),
    ])
    idx = {"TAIEX": pd.DataFrame({"date": ["2026-10-02", "2026-10-03"], "open": [100, 101], "close": [101, 105]}),
           "TPEX": pd.DataFrame({"date": ["2026-10-02", "2026-10-03"], "open": [200, 200], "close": [200, 190]})}
    out = signal_followup(events, prices, idx)
    a, b, c = out.iloc[0], out.iloc[1], out.iloc[2]
    assert a["buy_date"] == "2026-10-02" and a["buy_price"] == 10 and a["held_days"] == 2
    assert np.isclose(a["ret_now"], -10) and np.isclose(a["ret_max"], 10) and np.isclose(a["ret_min"], -10)
    assert np.isclose(a["index_ret"], 5) and np.isclose(a["excess"], -15)
    assert b["buy_price"] == 40  # 沒有開盤價（0）用收盤價
    assert np.isclose(b["ret_now"], 10) and np.isclose(b["index_ret"], -5)  # 上櫃和櫃買指數比
    assert pd.isna(c["ret_now"]) and pd.isna(c["buy_price"])
    s = followup_summary(out)
    assert s["n"] == 2 and s["pending"] == 1 and np.isclose(s["avg"], 0) and s["win"] == 0.5


def test_rule2_events_match_watchlist():
    """「今天出現法則2」的股票一定在最近的買點裡，而且日期是今天"""
    import fake_data
    from utils.analysis import GRANVILLE_DEFAULTS
    from utils.granville import rule1_watchlist, rule2_events
    t = fake_data.build_tables()
    prices = pd.DataFrame(t["daily_prices"])[["stock_id", "date", "close_price", "volume"]]
    ins = pd.DataFrame(t["stock_ins_daily"])
    ev = rule2_events(prices, ins=ins, min_volume=0, days=200, **GRANVILLE_DEFAULTS)
    watch = rule1_watchlist(prices, ins=ins, min_volume=0, **GRANVILLE_DEFAULTS)
    assert list(ev.columns) == ["stock_id", "setup_date", "signal_date"]
    assert (ev["signal_date"] > ev["setup_date"]).all()
    assert not ev.duplicated(["stock_id", "signal_date"]).any()
    if not watch.empty:
        for r in watch[watch["rule2_date"].notna()].itertuples():
            assert ((ev["stock_id"] == r.stock_id) & (ev["signal_date"] == r.rule2_date)).any()
    recent = rule2_events(prices, ins=ins, min_volume=0, days=5, **GRANVILLE_DEFAULTS)
    assert set(recent["signal_date"]) <= set(sorted(prices["date"].unique())[-5:])


def test_momentum_followup(use_db):
    use_db()
    from utils.analysis import momentum_followup
    date, df = momentum_followup()
    assert date is not None and not df.empty
    assert set(df["signal"]) <= {"兩者同時、52 週新高＋量增", "52 週新高＋量增", "剛符合趨勢模板",
                                 "兩者同時、52 週新高＋量增、剛符合趨勢模板", "52 週新高＋量增、剛符合趨勢模板"}
    assert df["ret_now"].notna().any()      # 前幾天的訊號已經買進
    assert df["ret_now"].isna().any()       # 今天的訊號還沒買進
    assert df["signal_date"].is_monotonic_decreasing


def test_granville_followup_runs(use_db):
    use_db()
    from utils.analysis import granville_followup
    date, df = granville_followup()
    assert date is not None


def test_momentum_prune_keeps_signal_rows():
    import momentum
    dates = [f"2026-09-{d:02d}" for d in range(1, 31)] + [f"2026-10-{d:02d}" for d in range(1, 11)]
    rows = []
    for d in dates:
        rows.append({"stock_id": "A", "date": d, "liquid": True, "breakout": False, "trend_new": False})
        rows.append({"stock_id": "B", "date": d, "liquid": True, "breakout": True, "trend_new": False})
        rows.append({"stock_id": "C", "date": d, "liquid": False, "breakout": False, "trend_new": False})
        rows.append({"stock_id": "D", "date": d, "liquid": True, "breakout": False, "trend_new": True})
    sb = FakeClient({"momentum_signals": rows})
    momentum.prune(sb, dates)
    left = pd.DataFrame(sb.tables["momentum_signals"])
    recent = dates[-momentum.KEEP_DAYS:]
    events = dates[-momentum.EVENT_KEEP_DAYS:]
    assert set(left[left["stock_id"].isin(["A", "C"])]["date"]) == set(recent)
    assert set(left[left["stock_id"] == "B"]["date"]) == set(events)
    assert set(left[left["stock_id"] == "D"]["date"]) == set(events)
