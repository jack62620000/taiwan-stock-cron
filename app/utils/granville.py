import numpy as np
import pandas as pd

# 葛蘭碧八大法則：用收盤價與移動平均線（均線）的相對位置、均線方向判斷買賣點
# 每條法則：(訊號, 說明, 可靠度)
# 可靠度依 2025-02 ~ 2026-10 回測（20 日均量 500 張以上、訊號隔天開盤買、持有 20 日和同一天全部股票平均比），
# 分成前後兩段期間檢驗：兩段方向一致的標「穩定」，方向相反、差距太小或樣本太少的標「參考」。
# 2026-10 改成隔天開盤買重測：法則 4 兩段都明顯較好（+2.6%／+1.4%）改為穩定；
# 法則 6 前段幾乎沒差（+0.1%）、只有後段偏弱，改為參考；法則 3 和平均一樣、法則 5 反而略好，維持參考。
RULES = {
    1: ("買進", "均線由下彎轉為走平或上揚，股價由下往上突破均線", "參考"),
    2: ("買進", "均線持續上揚（且月線在季線之上），股價短暫跌破均線後又站回均線之上", "穩定"),
    3: ("買進", "均線持續上揚，股價在均線之上回檔、靠近均線但沒跌破，再度上漲", "參考"),
    4: ("買進", "均線下彎，股價急跌、遠低於均線（負乖離過大），容易反彈", "穩定"),
    5: ("賣出", "均線由上揚轉為走平或下彎，股價由上往下跌破均線", "參考"),
    6: ("賣出", "均線持續下彎，股價短暫突破均線後又跌回均線之下", "參考"),
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
    停牌或沒有成交的日子（收盤價空值）視為股價不變來算均線，但當天不判斷法則。
    """
    traded = close.notna()
    close = close.ffill()
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
    valid = traded & ma.notna() & slope_before.notna()
    hits = {n: h & valid for n, h in hits.items()}
    # 法則 2 的四個條件分開保留，給觀察名單逐項顯示
    rule2_parts = [rising & (slope_before > 0), w_min < 0, close > ma, above_long]
    return {"ma": ma, "bias": bias, "slope": slope, "slope_before": slope_before, "hits": hits,
            "rule2_parts": rule2_parts}


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
    # MACD(12, 26, 9)，和 indicators.py 相同算法；沒有成交的日子視為股價不變
    close = close.ffill()
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

    c_t, c_y = close.iloc[-1], close.ffill().iloc[-2]  # 昨天沒有成交時，和最後成交價比
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
# 法則 1 觀察名單：法則 1 + 三個確認條件當起點，等法則 2 出現再買
# =====================================================================
WATCH_DAYS = 20             # 起點後幾個交易日內要出現法則 2，超過視為過期
HOLD_HINT = (5, 8)          # 法則 2 出現後的建議持有交易日數
# 回測是「法則 2 出現隔天開盤買進」，所以只有當天出現的才是買點
WATCH_STATUS = ["今天出現法則2", "等待法則2", "已錯過買點", "已過期"]
LATE_STATUS = ["已錯過買點", "已過期"]  # 預設收起來，不跟可操作的股票混在一起


def _setup_frames(prices, ins, min_volume, **rule_params):
    """觀察名單的起點：法則 1 + 外資 5 日買超 + MACD 柱 > 0 + 當日量 > 1.5 倍均量，且 20 日均量夠。
    回傳 (收盤寬表, 各法則命中寬表, 法則 2 的四個條件, 起點布林寬表)"""
    close = _pivot(prices, "close_price")
    vol = _pivot(prices, "volume").reindex(index=close.index, columns=close.columns) \
        if "volume" in prices.columns else None
    foreign = _pivot(ins, "foreign_net_shares") if ins is not None and not ins.empty else None
    frames = rule_frames(close, **rule_params)
    hits, parts = frames["hits"], frames["rule2_parts"]
    cf = confirm_frames(close, vol, foreign)
    setup = hits[1] & _confirmed(cf, ("foreign", "macd", "volume"), close.columns)
    if vol is not None:
        avg_vol = vol.fillna(0).rolling(20, min_periods=20).mean() / 1000
        setup &= avg_vol >= min_volume
    return close, hits, parts, setup


def rule1_watchlist(prices, ins=None, min_volume=500, watch_days=WATCH_DAYS,
                    show_expired_days=10, **rule_params):
    """
    近期符合「法則 1 + 外資 5 日買超 + MACD 柱 > 0 + 當日量 > 1.5 倍均量」的股票與後續狀態。

    回測（2025-12 ~ 2026-10，24 筆）：起點後 20 個交易日內出現法則 2 → 隔天開盤買，
    持有 5～8 個交易日的平均超額報酬約 +1.4%～+2.8%，中位數為正；樣本少，僅供參考。

    每檔股票只看最近一次起點。狀態：
      等待法則2：起點後還沒出現法則 2，且未超過 watch_days 個交易日
      今天出現法則2：明天開盤就是回測的買點
      已錯過買點：法則 2 在之前的交易日出現（只列出還在建議持有天數內的）
      已過期：起點後 watch_days 個交易日內都沒出現法則 2（只列出過期 show_expired_days 個交易日內的）
    """
    if prices.empty:
        return pd.DataFrame()
    close, hits, parts, setup = _setup_frames(prices, ins, min_volume, **rule_params)

    dates = close.index
    last = len(dates) - 1
    first = max(0, last - watch_days - show_expired_days)
    rows = []
    for sid in close.columns:
        idx = np.flatnonzero(setup[sid].to_numpy()[first:]) + first
        if len(idx) == 0:
            continue
        s = int(idx[-1])
        r2 = hits[2][sid].to_numpy()[s + 1: min(s + watch_days, last) + 1]
        found = np.flatnonzero(r2)
        c = close[sid].ffill()  # 今天沒有成交時顯示最後成交價
        row = {
            "stock_id": sid,
            "setup_date": dates[s],
            "setup_close": c.iloc[s],
            "close_price": c.iloc[-1],
            "change_since": (c.iloc[-1] / c.iloc[s] - 1) * 100,
            "days_since": last - s,
            "rule2_date": None,
            "rule2_day": None,
            "held_days": None,
        }
        # 法則 2 四個條件：已出現的看出現當天（全部符合），其餘看最新一天
        k = s + 1 + int(found[0]) if len(found) else last
        for i, part in enumerate(parts, 1):
            row[f"r2_c{i}"] = "符合" if bool(part[sid].iloc[k]) else "觀察"
        if len(found):
            held = last - k
            if held > HOLD_HINT[1]:
                continue  # 已超過建議持有天數，不再列出
            row.update(status="今天出現法則2" if held == 0 else "已錯過買點",
                       rule2_date=dates[k], rule2_day=k - s, held_days=held)
        elif last - s >= watch_days:
            if last - s > watch_days + show_expired_days:
                continue
            row["status"] = "已過期"
        else:
            row["status"] = "等待法則2"
        rows.append(row)
    return pd.DataFrame(rows)


def watch_note(row):
    """觀察名單每一列的提醒文字"""
    lo, hi = HOLD_HINT
    if row["status"] == "等待法則2":
        return f"已觀察 {row['days_since']} 天，再 {WATCH_DAYS - row['days_since']} 天內出現法則 2 才考慮買進"
    if row["status"] == "已過期":
        return f"{WATCH_DAYS} 個交易日內未出現法則 2，訊號失效"
    held = int(row["held_days"])
    if held == 0:
        return f"今天出現法則 2：可考慮明天開盤買進，建議持有 {lo}～{hi} 個交易日"
    return f"法則 2 後第 {held} 個交易日，已過買點；若當時有買進，建議持有到第 {lo}～{hi} 天"


def rule2_events(prices, ins=None, min_volume=500, days=20, watch_days=WATCH_DAYS, **rule_params):
    """
    最近 days 個交易日出現過的法則 2 買點（觀察名單起點後 watch_days 個交易日內第一次出現法則 2，
    和觀察名單「今天出現法則2」的判斷相同），給訊號事後追蹤用。
    回傳 stock_id、setup_date（起點）、signal_date（法則 2 出現日），依出現日由新到舊。
    prices 要比 days 多載入約 watch_days + 季線天數，起點和均線才算得出來。
    """
    cols = ["stock_id", "setup_date", "signal_date"]
    if prices.empty:
        return pd.DataFrame(columns=cols)
    close, hits, _, setup = _setup_frames(prices, ins, min_volume, **rule_params)
    dates = close.index
    first = len(dates) - days
    rows = {}
    for sid in close.columns:
        r2 = np.flatnonzero(hits[2][sid].to_numpy())
        for s in np.flatnonzero(setup[sid].to_numpy()):
            after = r2[(r2 > s) & (r2 <= s + watch_days)]
            if len(after) and after[0] >= first:
                rows.setdefault((sid, int(after[0])), dates[s])  # 同一個買點只算一次（取最早的起點）
    out = pd.DataFrame([(sid, setup_date, dates[k]) for (sid, k), setup_date in rows.items()], columns=cols)
    return out.sort_values(["signal_date", "stock_id"], ascending=[False, True], ignore_index=True)


def rule4_events(prices, min_volume=500, days=20, **rule_params):
    """
    最近 days 個交易日「剛出現」法則 4（急跌、負乖離過大；前一個交易日還沒有）的股票，給急跌反彈的事後追蹤用。
    同一檔連續好幾天都符合時只算第一天。20 日均量要 ≥ min_volume 張（同買賣預告）。
    回傳 stock_id、signal_date、bias（當天乖離率 %），依出現日由新到舊。
    """
    cols = ["stock_id", "signal_date", "bias"]
    if prices.empty:
        return pd.DataFrame(columns=cols)
    close = _pivot(prices, "close_price")
    frames = rule_frames(close, **rule_params)
    hit = frames["hits"][4]
    start = hit & ~hit.shift(1, fill_value=False)
    if "volume" in prices.columns:
        vol = _pivot(prices, "volume").reindex(index=close.index, columns=close.columns)
        start &= vol.fillna(0).rolling(20, min_periods=20).mean() / 1000 >= min_volume
    recent = start.iloc[-days:]
    out = recent.stack()
    out = out[out].reset_index()
    if out.empty:
        return pd.DataFrame(columns=cols)
    out.columns = ["signal_date", "stock_id", "_"]
    bias = frames["bias"]
    out["bias"] = [round(float(bias.at[d, s]), 2) for d, s in zip(out["signal_date"], out["stock_id"])]
    return out[cols].sort_values(["signal_date", "stock_id"], ascending=[False, True], ignore_index=True)


# =====================================================================
# 下一個交易日的觸發價：明天收盤到多少，就會出現某條法則
# =====================================================================
LIMIT_PCT = 0.10  # 台股漲跌幅限制，超出這個範圍的價格不可能出現


def _next_day_parts(close, ma_days=20, slope_days=5, lookback=5, near_pct=2.0, bias_pct=10.0, trend_days=60):
    """
    只算「明天」一天的法則時，跟明天收盤價無關的部分先算好（和 rule_frames 同樣的定義）。
    回傳 dict，交給 _next_day_hits 代入明天收盤價。
    """
    close = close.ffill()
    ma = close.rolling(ma_days, min_periods=ma_days).mean()
    bias = (close - ma) / ma * 100
    s = slope_days
    past = bias.iloc[-lookback:]  # 明天往回看的區間（不含明天）
    ma_long_prev = close.iloc[-(trend_days - 1):] if trend_days > 1 else close.iloc[0:0]
    return {
        "ma_days": ma_days, "trend_days": trend_days, "near_pct": near_pct, "bias_pct": bias_pct,
        "sum_ma": close.iloc[-(ma_days - 1):].sum(min_count=ma_days - 1),          # 明天均線要用的前 ma_days-1 天
        "sum_long": ma_long_prev.sum(min_count=trend_days - 1) if len(close) >= trend_days - 1 else None,
        "ma_back": ma.iloc[-s],                                                    # s 天前（相對明天）的均線
        "slope_before": ma.iloc[-s] - ma.iloc[-2 * s],                             # 明天的「一段時間前均線方向」
        "c_y": close.iloc[-1], "ma_y": ma.iloc[-1],
        "w_min": past.min(), "w_max": past.max(),
    }


def _next_day_hits(parts, p):
    """把明天收盤價 p（每檔股票一個值的 Series）代入，回傳 {法則: 布林 Series}，判斷方式同 rule_frames"""
    ma = (parts["sum_ma"] + p) / parts["ma_days"]
    if parts["sum_long"] is None:
        above_long = pd.Series(True, index=p.index)
    else:
        ma_long = (parts["sum_long"] + p) / parts["trend_days"]
        above_long = ma_long.isna() | (ma > ma_long)
    slope = ma - parts["ma_back"]
    sb = parts["slope_before"]
    bias = (p - ma) / ma * 100
    c_y, ma_y, w_min, w_max = parts["c_y"], parts["ma_y"], parts["w_min"], parts["w_max"]
    rising, falling = slope > 0, slope < 0
    cross_up = (c_y <= ma_y) & (p > ma)
    cross_down = (c_y >= ma_y) & (p < ma)
    near, big = parts["near_pct"], parts["bias_pct"]
    hits = {
        1: cross_up & (sb <= 0) & (slope >= 0),
        2: rising & (sb > 0) & (w_min < 0) & (p > ma) & above_long,
        3: rising & (w_min > 0) & (w_min <= near) & (bias > 0) & (p > c_y),
        4: falling & (bias <= -big),
        5: cross_down & (sb >= 0) & (slope <= 0),
        6: falling & (sb < 0) & (w_max > 0) & (p < ma),
        7: falling & (w_max < 0) & (w_max >= -near) & (bias < 0) & (p < c_y),
        8: rising & (bias >= big),
    }
    valid = p.notna() & ma.notna() & sb.notna()
    return {n: (h & valid).fillna(False).astype(bool) for n, h in hits.items()}


def next_day_triggers(prices, rules, side, stock_ids=None, steps=80, refine=14, **rule_params):
    """
    推算下一個交易日收盤價要到多少，才會出現 rules 裡任一條法則。

    假設明天收盤 = 今天收盤 ×（1 + g），g 在 ±10%（漲跌幅限制）內，先用格點找出會觸發的範圍，
    再用二分法找到邊界價格。
    side="買進"：回傳最低觸發價（明天收盤 ≥ 這個價就出現）；
    side="賣出"：回傳最高觸發價（明天收盤 ≤ 這個價就出現）。
    回傳 DataFrame：stock_id、trigger_price、trigger_pct（相對今天收盤 %）、rules（觸發價當下會出現的法則）。
    ±10% 內都不會出現的股票不列出。
    """
    empty = pd.DataFrame(columns=["stock_id", "trigger_price", "trigger_pct", "rules"])
    close = _pivot(prices, "close_price")
    if stock_ids is not None:
        keep = set(stock_ids)
        close = close[[c for c in close.columns if c in keep]]
    if close.empty:
        return empty
    last = close.ffill().iloc[-1]
    close = close.loc[:, last.notna().to_numpy()]
    last = last[close.columns]
    parts = _next_day_parts(close, **rule_params)

    def any_hit(g):
        h = _next_day_hits(parts, last * (1 + g))
        out = pd.Series(False, index=close.columns)
        for n in rules:
            out |= h[n]
        return out

    grid = np.linspace(-LIMIT_PCT, LIMIT_PCT, steps + 1)
    table = np.column_stack([any_hit(g).to_numpy() for g in grid])  # 股票 × 格點
    found = table.any(axis=1)
    if not found.any():
        return empty
    if side == "買進":
        i = table.argmax(axis=1)                                         # 最低會觸發的格點
        miss_i = i - 1                                                   # 再低一格（不觸發）
    else:
        i = table.shape[1] - 1 - table[:, ::-1].argmax(axis=1)          # 最高會觸發的格點
        miss_i = i + 1
    edge = found & (miss_i >= 0) & (miss_i < len(grid))
    hit_g = pd.Series(grid[i], index=close.columns)
    miss_g = pd.Series(np.where(edge, grid[np.clip(miss_i, 0, len(grid) - 1)], np.nan), index=close.columns)
    for _ in range(refine):  # 二分法逼近觸發和不觸發的邊界
        mid = ((hit_g + miss_g) / 2).where(edge, hit_g)
        h = any_hit(mid).to_numpy()
        hit_g = hit_g.where(~(edge & h), mid)
        miss_g = miss_g.where(~(edge & ~h), mid)
    price = last * (1 + hit_g)
    # 買進取無條件進位、賣出取無條件捨去到 0.01，讓列出的價格一定會觸發
    price = np.ceil(price * 100 - 1e-6) / 100 if side == "買進" else np.floor(price * 100 + 1e-6) / 100
    final = _next_day_hits(parts, price)
    ok = found & any_hit(price / last - 1).to_numpy()
    cols = close.columns[ok]
    return pd.DataFrame({
        "stock_id": cols,
        "trigger_price": price[cols].to_numpy(),
        "trigger_pct": (price[cols] / last[cols] - 1).to_numpy() * 100,
        "rules": [[n for n in rules if bool(final[n][sid])] for sid in cols],
    })


# 盤整股：近 CHOP_DAYS 個交易日股價穿越均線（站上或跌破）的次數達 CHOP_CROSSES 次
# 趨勢明確的股票從法則 1 到法則 2 通常只穿越 3～4 次（跌破 → 站上 → 回檔跌破 → 再站上）
# 回測（2025-10 ~ 2026-10 全部法則 2 訊號，約 1 萬筆，隔天開盤買、持有 6 日）：
# 穿越 0～2 次平均 +0.33%、3～4 次 +0.83%、5 次 +0.65%、6～7 次 +1.13%、8 次以上 +0.86%，
# 盤整股並沒有比較差，所以只當參考標示，不影響排序
CHOP_DAYS = 40
CHOP_CROSSES = 6


def ma_cross_counts(prices, ma_days=20, days=CHOP_DAYS):
    """每檔股票近 days 個交易日收盤價穿越 ma_days 日均線的次數（Series，index 為 stock_id）。"""
    if prices.empty:
        return pd.Series(dtype=int)
    close = _pivot(prices, "close_price").ffill()
    ma = close.rolling(ma_days, min_periods=ma_days).mean()
    side = np.sign(close - ma).where(ma.notna())
    side = side.replace(0, np.nan).ffill()  # 剛好等於均線的日子沿用前一天的位置
    recent = side.iloc[-(days + 1):]
    crosses = (recent != recent.shift(1)) & recent.notna() & recent.shift(1).notna()
    return crosses.sum().astype(int)


# 停損：買進價往下 STOP_LOSS_PCT %，收盤跌破就出場
# 回測（同上，法則 2 持有 6 日）：不停損平均 +0.86%、最差 -38%；停損 8% 平均 +0.66%、最差 -16.6%；
# 停損 5% 平均 +0.52%（一半的交易被洗出場）。8% 少賺一點，但能擋掉大虧
STOP_LOSS_PCT = 8.0

# ATR 停損：停損距離取「ATR_MULT 倍 ATR」和「STOP_LOSS_PCT %」比較小的那個，收盤跌破就出場。
# ATR（平均真實波幅）是近 ATR_DAYS 個交易日每天的波動幅度平均，代表這檔股票平常一天會動多少錢。
# 回測（2025/1～2026/10，20 日均量 500 張以上、隨機進場持有 20 日）：
# 固定 8% 平均 +1.05%、最差 -17%；純 3 倍 ATR 平均 +1.32%、最差 -46%（高波動股停損拉太寬）；
# 3 倍 ATR、最多 8% 平均 +1.05%、最差 -17%。大虧上限不變，波動小的股票停損比較貼近
ATR_DAYS = 14
ATR_MULT = 3.0
STOP_RULE = f"預計買進價往下 {ATR_MULT:g} 倍 ATR（近 {ATR_DAYS} 日平均波動），最多 {STOP_LOSS_PCT:g}%"


def atr(prices, days=ATR_DAYS):
    """
    每檔股票最近 days 個交易日的 ATR（元），資料不足 days 天的不列出。
    prices：stock_id、date、high_price、low_price、close_price（還原權值）。
    """
    if prices is None or prices.empty or not {"high_price", "low_price", "close_price"} <= set(prices.columns):
        return pd.Series(dtype=float)
    p = prices.sort_values(["stock_id", "date"])
    high, low, close = (pd.to_numeric(p[c], errors="coerce") for c in ("high_price", "low_price", "close_price"))
    prev = close.groupby(p["stock_id"]).shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1).where(high.notna() & low.notna())
    tr = tr.groupby(p["stock_id"]).tail(days)
    stats = tr.groupby(p.loc[tr.index, "stock_id"]).agg(["mean", "count"])
    return stats.loc[stats["count"] >= days, "mean"]


def stop_price(buy, atr_value=None):
    """停損價：買進價往下 ATR_MULT 倍 ATR，最多 STOP_LOSS_PCT %；沒有 ATR 就用 STOP_LOSS_PCT %"""
    dist = buy * STOP_LOSS_PCT / 100
    if atr_value is not None and pd.notna(atr_value) and atr_value > 0:
        dist = min(dist, ATR_MULT * float(atr_value))
    return round(buy - dist, 2)
RS_DAYS = 20


def relative_strength(prices, days=RS_DAYS):
    """
    每檔股票近 days 個交易日漲跌幅減去同期全部股票的平均漲跌幅（%），正值代表比大盤強。
    大盤用全部股票等權平均。
    """
    if prices.empty:
        return pd.Series(dtype=float)
    close = _pivot(prices, "close_price").ffill()
    if len(close) <= days:
        return pd.Series(dtype=float)
    ret = (close.iloc[-1] / close.iloc[-(days + 1)] - 1) * 100
    ret = ret.replace([np.inf, -np.inf], np.nan).dropna()
    return ret - ret.mean()
