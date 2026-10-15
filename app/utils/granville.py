import numpy as np
import pandas as pd

# 葛蘭碧八大法則：用收盤價與移動平均線（均線）的相對位置、均線方向判斷買賣點
# 每條法則：(訊號, 說明, 可靠度)
# 可靠度依 2025-09 ~ 2026-10 回測（20 日均量 500 張以上、持有 10 日相對大盤的超額報酬），
# 分成前後兩段期間檢驗：兩段方向一致的標「穩定」，方向相反或樣本太少的標「參考」。
RULES = {
    1: ("買進", "均線由下彎轉為走平或上揚，股價由下往上突破均線", "參考"),
    2: ("買進", "均線持續上揚（且月線在季線之上），股價短暫跌破均線後又站回均線之上", "穩定"),
    3: ("買進", "均線持續上揚，股價在均線之上回檔、靠近均線但沒跌破，再度上漲", "參考"),
    4: ("買進", "均線下彎，股價急跌、遠低於均線（負乖離過大），容易反彈", "參考"),
    5: ("賣出", "均線由上揚轉為走平或下彎，股價由上往下跌破均線", "參考"),
    6: ("賣出", "均線持續下彎，股價短暫突破均線後又跌回均線之下", "穩定"),
    7: ("賣出", "均線持續下彎，股價在均線之下反彈、靠近均線但沒突破，再度下跌", "穩定"),
    # 原典把正乖離過大視為賣訊，但回測兩段期間都是續漲（乖離越大漲越多），所以只當過熱提醒
    8: ("過熱", "均線上揚，股價急漲、遠高於均線（正乖離過大）；回測多半續漲，僅提醒追高風險", "穩定"),
}

# 預設只顯示回測穩定的法則
STABLE_RULES = [n for n, (_, _, level) in RULES.items() if level == "穩定"]


def _pivot(prices, col):
    return prices.assign(**{col: pd.to_numeric(prices[col], errors="coerce")}) \
        .pivot_table(index="date", columns="stock_id", values=col, aggfunc="last") \
        .sort_index()


def rule_frames(close, ma_days=20, slope_days=5, lookback=5,
                near_pct=2.0, bias_pct=10.0, trend_days=60):
    """
    對「每一天」判斷八大法則。close 是 日期 × 股票 的收盤價寬表。
    回傳 dict：ma、bias、slope、slope_before（寬表）與 hits（{法則編號: 布林寬表}）。
    """
    ma = close.rolling(ma_days, min_periods=ma_days).mean()
    ma_long = close.rolling(trend_days, min_periods=trend_days).mean()
    slope = ma - ma.shift(slope_days)
    slope_before = slope.shift(slope_days)  # 一段時間前的均線方向，判斷「轉折」用
    bias = (close - ma) / ma * 100
    c_y, ma_y = close.shift(1), ma.shift(1)

    # 往回看的區間（不含今天）
    past = bias.shift(1).rolling(lookback, min_periods=1)
    w_min, w_max = past.min(), past.max()
    above_long = ma_long.isna() | (ma > ma_long)

    rising = slope > 0
    falling = slope < 0
    cross_up = (c_y <= ma_y) & (close > ma)
    cross_down = (c_y >= ma_y) & (close < ma)

    hits = {
        1: cross_up & (slope_before <= 0) & (slope >= 0),
        2: rising & (slope_before > 0) & (w_min < 0) & (close > ma) & above_long,
        3: rising & (w_min > 0) & (w_min <= near_pct) & (bias > 0) & (close > c_y),
        4: falling & (bias <= -bias_pct),
        5: cross_down & (slope_before >= 0) & (slope <= 0),
        6: falling & (slope_before < 0) & (w_max > 0) & (close < ma),
        7: falling & (w_max < 0) & (w_max >= -near_pct) & (bias < 0) & (close < c_y),
        8: rising & (bias >= bias_pct),
    }
    # 資料不足（收盤價、均線或一段時間前的均線方向算不出來）的日子不判斷
    valid = close.notna() & ma.notna() & slope_before.notna()
    hits = {n: h & valid for n, h in hits.items()}
    return {"ma": ma, "bias": bias, "slope": slope, "slope_before": slope_before, "hits": hits}


# 買進確認條件：只加在買進法則上。依 2025-12 ~ 2026-10 回測，
# 單獨使用效果不穩，三個一起用時三段期間的超額報酬都明顯改善（詳見頁面說明）。
CONFIRMS = {
    "foreign": "外資 5 日買超",
    "macd": "MACD 柱狀體 > 0",
    "volume": "當日量 > 1.5 倍 20 日均量",
}
VOLUME_RATIO = 1.5


def confirm_frames(close, volume=None, foreign=None):
    """
    買進確認條件的 日期 × 股票 寬表。
    回傳 dict：values（顯示用數值：外資 5 日買超張數、MACD 柱、量比）與 hits（{條件: 布林寬表}）。
    缺資料的條件不會出現在 hits 裡。
    """
    values, hits = {}, {}
    # MACD(12, 26, 9)，和 indicators.py 相同算法
    ema_fast = close.ewm(span=12, adjust=False).mean()
    ema_slow = close.ewm(span=26, adjust=False).mean()
    dif = ema_fast - ema_slow
    hist = dif - dif.ewm(span=9, adjust=False).mean()
    values["macd_hist"] = hist
    hits["macd"] = hist > 0
    if volume is not None:
        vol = volume.reindex(index=close.index, columns=close.columns)
        prev_avg = vol.fillna(0).shift(1).rolling(20, min_periods=20).mean()
        ratio = vol / prev_avg.where(prev_avg > 0)
        values["vol_ratio"] = ratio
        hits["volume"] = ratio > VOLUME_RATIO
    if foreign is not None:
        f = foreign.reindex(index=close.index, columns=close.columns).fillna(0)
        f5 = f.rolling(5, min_periods=1).sum() / 1000  # 股 → 張
        values["foreign_5d"] = f5
        hits["foreign"] = f5 > 0
    return {"values": values, "hits": hits}


def granville_signals(prices, ma_days=20, slope_days=5, lookback=5,
                      near_pct=2.0, bias_pct=10.0, trend_days=60, ins=None, confirms=()):
    """
    依葛蘭碧八大法則，判斷每檔股票在最新一個交易日符合哪幾條。

    prices：含 stock_id、date、close_price 欄位，每檔股票每天一筆。
    ma_days：均線天數。
    slope_days：均線方向用「今天的均線 − slope_days 天前的均線」判斷，> 0 上揚、< 0 下彎。
    lookback：法則 2、3、6、7 往回看幾天內的走勢。
    near_pct：法則 3、7「靠近均線」的距離（乖離率 %）。
    bias_pct：法則 4、8「乖離過大」的門檻（乖離率 %）。
    trend_days：長期均線（季線）天數。法則 2 另外要求均線在季線之上，回測兩段期間都比較好；
                資料不足算不出季線的股票不套用這個條件。

    ins：法人買賣超（stock_id、date、foreign_net_shares），算外資 5 日買超用，可省略。
    confirms：要套用的買進確認條件（CONFIRMS 的 key）；不符合的股票不列入買進法則 1～4。

    回傳每檔股票一筆：收盤價、漲跌、均線、乖離率、均線方向、20 日均量（張）、
    外資 5 日買超（張）、MACD 柱、量比、符合的法則（list）。
    資料不足的股票（例如新上市）不列出。
    """
    if prices.empty:
        return pd.DataFrame()

    close = _pivot(prices, "close_price")
    f = rule_frames(close, ma_days, slope_days, lookback, near_pct, bias_pct, trend_days)

    c_t, c_y = close.iloc[-1], close.iloc[-2]
    ma_t = f["ma"].iloc[-1]
    slope_t = f["slope"].iloc[-1]
    rising, falling = slope_t > 0, slope_t < 0

    vol = _pivot(prices, "volume").reindex(index=close.index, columns=close.columns) \
        if "volume" in prices.columns else None
    avg_vol20 = (vol.fillna(0).tail(20).mean() / 1000).round(0) if vol is not None \
        else pd.Series(np.nan, index=close.columns)  # 股 → 張
    foreign = _pivot(ins, "foreign_net_shares") if ins is not None and not ins.empty else None
    cf = confirm_frames(close, vol, foreign)

    def last_value(name):
        wide = cf["values"].get(name)
        return wide.iloc[-1].reindex(close.columns).values if wide is not None else np.nan

    out = pd.DataFrame({
        "stock_id": close.columns,
        "close_price": c_t.values,
        "change": (c_t - c_y).round(2).values,
        "ma": ma_t.round(2).values,
        "bias": f["bias"].iloc[-1].round(2).values,
        "ma_trend": np.select([rising, falling], ["上揚", "下彎"], "走平"),
        "avg_volume_20": avg_vol20.values,
        "foreign_5d": last_value("foreign_5d"),
        "macd_hist": last_value("macd_hist"),
        "vol_ratio": last_value("vol_ratio"),
    })
    valid = (c_t.notna() & ma_t.notna() & f["slope_before"].iloc[-1].notna()).values
    out = out[valid].reset_index(drop=True)
    today = {n: h.iloc[-1] for n, h in f["hits"].items()}
    ok = _confirmed(cf, confirms, close.columns)
    if ok is not None:
        for n, side in ((n, RULES[n][0]) for n in today):
            if side == "買進":
                today[n] = today[n] & ok.iloc[-1]
    out["rules"] = [
        [n for n, hit in today.items() if bool(hit.get(sid, False))]
        for sid in close.columns[valid]
    ]
    return out


def _confirmed(cf, confirms, columns):
    """所有選取的確認條件都成立的布林寬表；沒選條件時回傳 None。缺資料的條件視為不成立。"""
    if not confirms:
        return None
    ok = None
    for name in confirms:
        h = cf["hits"].get(name)
        h = h.reindex(columns=columns).fillna(False) if h is not None else False
        ok = h if ok is None else ok & h
    if isinstance(ok, bool):  # 選的條件全都沒有資料
        return pd.DataFrame(False, index=cf["values"]["macd_hist"].index, columns=columns)
    return ok


# =====================================================================
# 模擬交易（歷史回測）
# =====================================================================
BUY_FEE = 0.001425          # 手續費（買、賣各收一次，未打折）
SELL_TAX = 0.003            # 證交稅（賣出時收）


def simulate_trades(prices, buy_rules, sell_rules, hold_days=20, stop_loss_pct=None,
                    min_volume=500, start_date=None, ins=None, confirms=(), **rule_params):
    """
    依葛蘭碧法則模擬買賣，每檔股票同一時間最多持有一筆。

    進場：收盤後出現 buy_rules 任一條，且 20 日均量 ≥ min_volume 張 → 隔天開盤價買進。
    出場（先碰到哪個算哪個）：
      - 收盤出現 sell_rules 任一條 → 隔天開盤價賣出
      - 收盤價跌破進場價 stop_loss_pct% → 隔天開盤價賣出
      - 持有滿 hold_days 個交易日 → 當天收盤價賣出
    開盤價缺漏時改用收盤價。資料最後一天還沒出場的，用最後收盤價計算未實現報酬。
    報酬已扣手續費（買賣各 0.1425%）與證交稅（0.3%）。
    confirms：買進還要同時符合的確認條件（CONFIRMS 的 key），外資條件需要傳入 ins。

    回傳 (trades, benchmark)：
      trades：每筆交易一列；benchmark：同期間全部股票等權平均的累積指數（日期 → 指數），
      用來算每筆交易同一段期間「買大盤」的報酬。
    """
    close = _pivot(prices, "close_price")
    open_ = _pivot(prices, "open_price").reindex(index=close.index, columns=close.columns)
    open_ = open_.where(open_ > 0).fillna(close)
    vol = _pivot(prices, "volume").reindex(index=close.index, columns=close.columns)
    avg_vol = vol.fillna(0).rolling(20, min_periods=20).mean() / 1000

    hits = rule_frames(close, **rule_params)["hits"]
    empty = pd.DataFrame(False, index=close.index, columns=close.columns)
    buy = empty.copy()
    for n in buy_rules:
        buy |= hits[n]
    buy &= avg_vol >= min_volume
    foreign = _pivot(ins, "foreign_net_shares") if ins is not None and not ins.empty else None
    ok = _confirmed(confirm_frames(close, vol, foreign), confirms, close.columns)
    if ok is not None:
        buy &= ok
    sell = empty.copy()
    for n in sell_rules:
        sell |= hits[n]

    # 等權平均指數：每天所有股票漲跌幅的平均，連乘起來
    daily = close.pct_change(fill_method=None).clip(-0.11, 0.11)  # 擋掉除權息、減資造成的跳空
    benchmark = (1 + daily.mean(axis=1).fillna(0)).cumprod()

    dates = close.index
    first = 0 if start_date is None else int(np.searchsorted(dates, start_date))
    sl = None if not stop_loss_pct else 1 - stop_loss_pct / 100
    trades = []
    for sid in close.columns:
        b = buy[sid].to_numpy()
        if not b[first:-1].any():
            continue
        s = sell[sid].to_numpy()
        c = close[sid].to_numpy()
        o = open_[sid].to_numpy()
        i = first
        n = len(dates)
        while i < n - 1:
            if not b[i] or np.isnan(o[i + 1]):
                i += 1
                continue
            entry_i, entry_px = i + 1, o[i + 1]
            exit_i, exit_px, reason = None, None, None
            j = entry_i
            while j < n:
                if np.isnan(c[j]):
                    j += 1
                    continue
                if j - entry_i + 1 >= hold_days:
                    exit_i, exit_px, reason = j, c[j], f"持有滿 {hold_days} 天"
                    break
                if j + 1 < n and (s[j] or (sl is not None and c[j] <= entry_px * sl)):
                    exit_i, exit_px = j + 1, o[j + 1]
                    reason = "賣出訊號" if s[j] else f"停損 {stop_loss_pct:g}%"
                    break
                j += 1
            if exit_i is None:
                last = n - 1
                while last > entry_i and np.isnan(c[last]):
                    last -= 1
                exit_i, exit_px, reason = last, c[last], "未平倉"
            gross = exit_px / entry_px
            net = gross * (1 - BUY_FEE - SELL_TAX) / (1 + BUY_FEE) - 1
            trades.append({
                "stock_id": sid,
                "signal_date": dates[i],
                "entry_date": dates[entry_i],
                "entry_price": float(entry_px),
                "exit_date": dates[exit_i],
                "exit_price": float(exit_px),
                "hold_days": exit_i - entry_i + 1,
                "return_pct": net * 100,
                "benchmark_pct": (benchmark.iloc[exit_i] / benchmark.iloc[entry_i - 1] - 1) * 100,
                "exit_reason": reason,
                "rules": [k for k in buy_rules if hits[k][sid].iloc[i]],
            })
            if reason == "未平倉":
                break
            i = exit_i  # 出場當天收盤若又出現買進訊號，隔天可以再進場
    return pd.DataFrame(trades), benchmark
