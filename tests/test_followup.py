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
    from utils.analysis import MOMENTUM_FOLLOW_PICKS
    assert all(set(s.split("、")) <= set(MOMENTUM_FOLLOW_PICKS) for s in df["signal"])
    assert df["signal"].str.contains("趨勢中回檔再起").any()
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
        rows.append({"stock_id": "E", "date": d, "liquid": True, "breakout": False, "trend_new": False, "pullback": True})
    for r in rows:
        r.setdefault("pullback", False)
    sb = FakeClient({"momentum_signals": rows})
    momentum.prune(sb, dates)
    left = pd.DataFrame(sb.tables["momentum_signals"])
    recent = dates[-momentum.KEEP_DAYS:]
    events = dates[-momentum.EVENT_KEEP_DAYS:]
    assert set(left[left["stock_id"].isin(["A", "C"])]["date"]) == set(recent)
    assert set(left[left["stock_id"] == "B"]["date"]) == set(events)
    assert set(left[left["stock_id"] == "D"]["date"]) == set(events)
    assert set(left[left["stock_id"] == "E"]["date"]) == set(events)


def test_momentum_pullback():
    """上升趨勢裡回檔碰到布林下軌，之後收盤站回中軌的那一天才算趨勢中回檔再起"""
    import numpy as np
    import momentum
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-01-01", periods=300)]
    closes = list(np.linspace(10, 30, 290)) + [29.0, 28.0, 27.5, 27.6, 28.2, 29.0, 30.5, 30.8, 31.0, 31.2]
    rows = [{"stock_id": "1234", "date": d, "high_price": c * 1.01, "low_price": c * 0.99, "close_price": c,
             "volume": 1_000_000} for d, c in zip(days, closes)]
    out = momentum.compute(pd.DataFrame(rows), pd.DataFrame(columns=["stock_id", "date", "event_type", "factor"]),
                           days[-10:])
    assert [r["date"] for r in out if r["pullback"]] == [days[-4]]


def test_momentum_choices_drop_hot():
    """離 50 日線 30% 以上的漲多股不列入任何分類"""
    from utils.momentum import hot_count, momentum_choices, momentum_table
    liq = pd.DataFrame({
        "stock_id": ["A", "B", "C"], "close_price": [110.0, 135.0, 120.0], "ma50": [100.0, 100.0, 100.0],
        "high_52w": [111.0, 136.0, 121.0], "atr14": [2.0, 2.0, 2.0], "rs_rating": [80, 95, 90], "vol_ratio": [2.0, 2.0, 1.0],
        "trend_template": [True, True, True], "breakout": [True, True, False], "trend_new": [False, False, False],
        "pullback": [False, False, True]})
    choices = momentum_choices(liq)
    assert list(liq[choices["兩者同時"]]["stock_id"]) == ["A"]
    assert list(liq[choices["符合趨勢模板（全部）"]]["stock_id"]) == ["A", "C"]
    assert list(liq[choices["趨勢中回檔再起"]]["stock_id"]) == ["C"]
    assert hot_count(liq) == 1
    assert momentum_table(liq)["off_ma50"].round(1).tolist() == [35.0, 20.0, 10.0]


def test_momentum_compute_no_infinite_values():
    """前 50 日都沒成交、今天才有量的股票，量比不能是無限大（寫進資料庫會失敗）"""
    import json
    import momentum
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-01-01", periods=300)]
    rows = []
    for i, d in enumerate(days):
        rows.append({"stock_id": "1234", "date": d, "high_price": 10.5, "low_price": 9.5, "close_price": 10.0,
                     "volume": 0 if i < 299 else 1_000_000})
    out = momentum.compute(pd.DataFrame(rows), pd.DataFrame(columns=["stock_id", "date", "event_type", "factor"]),
                           days[-1:])
    assert out and out[0]["vol_ratio"] is None and out[0]["pullback"] is False
    json.dumps(out, allow_nan=False)


# ===== 大盤季線提醒 =====
def _index(closes):
    return pd.DataFrame({"date": pd.bdate_range("2026-01-01", periods=len(closes)).strftime("%Y-%m-%d"),
                         "close": closes})


def test_market_regime_below_and_above():
    from utils.analysis import MARKET_MA, market_regime, market_warning
    down = market_regime(_index([100.0] * (MARKET_MA - 1) + [90.0]))
    assert down["below"] and down["gap"] < 0
    assert "季線下" in market_warning(down)
    up = market_regime(_index([100.0] * (MARKET_MA - 1) + [110.0]))
    assert not up["below"] and market_warning(up) is None
    assert market_regime(_index([100.0] * 10)) is None
    assert market_regime(pd.DataFrame()) is None and market_warning(None) is None


def test_line_lists_add_warning():
    from line_lists import add_warning
    msgs = [{"type": "text", "text": "標題"}, {"type": "flex"}]
    add_warning(msgs, None)
    assert msgs[0]["text"] == "標題"
    add_warning(msgs, "⚠️ 大盤在季線下")
    assert msgs[0]["text"].startswith("⚠️ 大盤在季線下\n\n標題")


def test_granville_buy_table_all_today():
    """候選股全部是「今天出現法則2」（沒有等待中的觸發價）時不會出錯，距離%、觸發價留白"""
    from utils.analysis import granville_buy_table
    cand = pd.DataFrame([
        {"stock_id": sid, "stock_name": sid, "industry": "半導體業", "status": "今天出現法則2", "days_since": 1,
         "close_price": 100.0, "setup_date": "2026-10-28", "r2_c1": "符合", "r2_c2": "符合", "r2_c4": "符合"}
        for sid in ["2330", "2303"]
    ])
    trig = pd.DataFrame(columns=["stock_id", "trigger_price", "trigger_pct"]).set_index("stock_id")
    table, triggers = granville_buy_table(cand, trig, pd.Series(dtype=float), {}, {"2330": 1.5}, {})
    assert list(table["代號"]) == ["2330", "2303"]  # 比大盤強的排前面
    assert table["距離%"].isna().all() and table["觸發價"].isna().all()
    assert triggers == {}
