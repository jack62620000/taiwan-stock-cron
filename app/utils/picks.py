"""
選股頁的買賣規則：每日選股（v5）、動能選股、葛蘭碧合併成一套。

回測（2025-01～2026-10，隔天開盤買、扣交易成本，10 個名額各放 1/10 資金）比較過 10 種買法 × 6 種賣法，
最好的是：
- 買進：大盤（加權指數）收盤在 60 日均線之上時，隔天開盤買每日選股前 10 名（已持有的跳過、名額滿了不買）
- 賣出：買進後第 10 個交易日開盤賣出（固定天數）
總報酬 +476%（同期加權指數 +105%），最大回撤 −15.3%，勝率 57%。
動能、布林、葛蘭碧加進分數（v6）回測都沒有比較好，所以只當標籤參考。
"""
import pandas as pd

SLOTS = 10            # 同時最多持有幾檔（每檔 1/10 資金）
HOLD = 10             # 買進後第幾個交易日開盤賣出
TOP_N = 10            # 每天買進候選：每日選股前幾名
START = "2026-06-01"  # 從這天開始照規則模擬（評分排名從這天起都有保留），起點固定，每天看到的持股才會一致

BACKTEST_MD = """
| 設定（大盤在季線上才買） | 總報酬 | 年化 | 最大回撤 | 勝率 |
|---|---|---|---|---|
| **前 10 名、持有 10 天（採用）** | **+476%** | **165%** | **−15.3%** | **57%** |
| 前 10 名、持有 5 天 | +294% | — | — | — |
| 前 10 名、持有 20 天 | +306% | — | −20.9% | — |
| 前 10 名、持有 3 天 | +130% | — | — | — |
| 同期加權指數 | +105% | — | −26.7% | — |

- 期間 2025-01～2026-10，評分隔天開盤買、到期那天開盤賣，一買一賣扣約 0.6% 成本。
- 前半段 +74%、後半段 +231%，兩段都贏大盤；但這段期間是大多頭，參數也是用同一段資料挑出來的，以後的報酬可能明顯較低。
- 把動能（趨勢模板、RS、新高）、布林收斂、葛蘭碧法則 2 加分或賣訊扣分做成 v6，15 種設定平均都沒有比 v5 好，所以只當標籤。
- 葛蘭碧法則 4（急跌反彈）當買點每一種組合都賠錢；用跌破月線當賣出也比較差。
"""

RULES_MD = f"""
**每天收盤後要做的事**
1. 看大盤：加權指數收盤在 60 日均線（季線）之上才買新股票；在季線下就不買，手上的照時間賣。
2. 賣出：買進後第 {HOLD} 個交易日開盤賣出，不管賺賠。
3. 買進：空出來的名額，隔天開盤買每日選股前 {TOP_N} 名（已經持有的跳過，分數高的先買）。
4. 最多同時持有 {SLOTS} 檔，每檔約放總資金的 1/{SLOTS}。

**標籤只是參考，不影響買賣**
- 🚀 趨勢模板、新高量增、回檔再起：動能選股的訊號。
- 🎯 布林收斂：符合趨勢模板，而且布林通道寬度在近 120 日最窄的 20%（回測 60 日超額 +13%）。
- 📐 法則2：近 5 個交易日出現過葛蘭碧法則 2。
- ⚠️ 漲多：離 50 日線 30% 以上；⚠️ 葛蘭碧賣訊：今天出現法則 5、6、7。
"""


def market_ok_by_date(index_df, ma_days=60):
    """加權指數每天收盤是否在 ma_days 日均線之上：{日期: bool}，均線算不出來的日子為 False"""
    if index_df is None or index_df.empty:
        return {}
    df = index_df.sort_values("date")
    close = pd.to_numeric(df["close"], errors="coerce")
    ma = close.rolling(ma_days, min_periods=ma_days).mean()
    return {str(d)[:10]: bool(c > m) if pd.notna(m) else False for d, c, m in zip(df["date"], close, ma)}


def replay(dates, tops, market_ok, slots=SLOTS, hold=HOLD):
    """
    照規則從 dates[0] 開始模擬每天的買賣（和回測的 sim.simulate 同樣做法）。
    dates：評分日，由舊到新；tops：{評分日: [前 N 名 stock_id，依排名]}；market_ok：{評分日: 大盤在季線上}。
    第 i 天開盤：先賣持有滿 hold 天的，再用前一天的前 N 名補空出來的名額。
    回傳 (trades, plan)：
    trades 每筆 {stock_id, signal_date, buy_date, buy_i, sell_date（還沒賣是 None）}；
    plan 是明天開盤要做的事 {sell: [持股], buy: [stock_id], hold: [持股], market_ok, free}
    """
    pos, closed = [], []
    for i in range(1, len(dates)):
        d, prev = dates[i], dates[i - 1]
        keep = []
        for p in pos:
            if i - p["buy_i"] >= hold:
                closed.append({**p, "sell_date": d})
            else:
                keep.append(p)
        pos = keep
        free = slots - len(pos)
        if free > 0 and market_ok.get(prev):
            held = {p["stock_id"] for p in pos}
            for sid in [s for s in tops.get(prev, []) if s not in held][:free]:
                pos.append({"stock_id": sid, "signal_date": prev, "buy_date": d, "buy_i": i, "sell_date": None})
    n = len(dates)  # 明天的位置
    sell = [p for p in pos if n - p["buy_i"] >= hold]
    remain = [p for p in pos if n - p["buy_i"] < hold]
    free = slots - len(remain)
    ok = bool(dates) and bool(market_ok.get(dates[-1]))
    buy = []
    if free > 0 and ok:
        held = {p["stock_id"] for p in remain}
        buy = [s for s in tops.get(dates[-1], []) if s not in held][:free]
    for p in pos:
        p["day"] = n - p["buy_i"]  # 今天是持有第幾天（買進當天算第 1 天）
    return closed + pos, {"sell": sell, "buy": buy, "hold": remain, "market_ok": ok, "free": free}


def trade_returns(trades, prices, cost_pct):
    """
    每筆交易的報酬（%，還原權值，扣交易成本）。prices：stock_id、date、open_price、close_price（還原權值）。
    已賣出用賣出日開盤價；還沒賣用最新收盤價。回傳 DataFrame，多了 buy_price、now_price、ret 欄位。
    """
    df = pd.DataFrame(trades, columns=["stock_id", "signal_date", "buy_date", "buy_i", "sell_date", "day"])
    if df.empty:
        return df.assign(buy_price=None, now_price=None, ret=None)
    px = prices.copy()
    for c in ["open_price", "close_price"]:
        px[c] = pd.to_numeric(px[c], errors="coerce")
    opens = px.set_index(["stock_id", "date"])["open_price"]
    last_close = px.dropna(subset=["close_price"]).sort_values("date").groupby("stock_id")["close_price"].last()
    df["buy_price"] = [opens.get((s, d)) for s, d in zip(df["stock_id"], df["buy_date"])]
    df["now_price"] = [opens.get((s, d)) if pd.notna(d) else last_close.get(s)
                       for s, d in zip(df["stock_id"], df["sell_date"])]
    for c in ["buy_price", "now_price"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["ret"] = (df["now_price"] / df["buy_price"] - 1) * 100 - cost_pct
    return df


def stock_tags(ids, prices, momentum):
    """
    每檔股票的參考標籤 {stock_id: [標籤]}。
    prices：stock_id、date、close_price（還原權值，約 120 個交易日以上，布林收斂才算得準）；
    momentum：最新一天的 momentum_signals（trend_template、breakout、pullback、close_price、ma50）。
    """
    from utils.granville import rule_frames
    from utils.momentum import HOT_PCT, off_ma50
    tags = {sid: [] for sid in ids}
    mom = momentum.set_index("stock_id") if momentum is not None and not momentum.empty else pd.DataFrame()
    tt = set()
    if not mom.empty:
        hot = off_ma50(mom.reset_index()).set_axis(mom.index)
        for sid in ids:
            if sid not in mom.index:
                continue
            r = mom.loc[sid]
            if bool(r.get("trend_template")):
                tags[sid].append("🚀 趨勢模板")
                tt.add(sid)
            if bool(r.get("breakout")):
                tags[sid].append("🚀 新高量增")
            if bool(r.get("pullback")):
                tags[sid].append("🚀 回檔再起")
            if pd.notna(hot.get(sid)) and hot.get(sid) >= HOT_PCT:
                tags[sid].append("⚠️ 漲多")
    if prices is not None and not prices.empty:
        close = prices.assign(close_price=pd.to_numeric(prices["close_price"], errors="coerce")) \
            .pivot_table(index="date", columns="stock_id", values="close_price", aggfunc="last").sort_index()
        hits = rule_frames(close)["hits"]
        rule2 = hits[2] & ~hits[2].shift(1, fill_value=False)
        rule2_recent = rule2.tail(5).any()
        sell = (hits[5] | hits[6] | hits[7]).iloc[-1] if len(close) else pd.Series(dtype=bool)
        c = close.ffill()
        mid = c.rolling(20, min_periods=20).mean()
        width = 4 * c.rolling(20, min_periods=20).std(ddof=0) / mid * 100
        squeeze = (width.rolling(120, min_periods=60).rank(pct=True) <= 0.2).iloc[-1] if len(c) else pd.Series(dtype=bool)
        for sid in ids:
            if sid in tt and bool(squeeze.get(sid, False)):
                tags[sid].append("🎯 布林收斂")
            if bool(rule2_recent.get(sid, False)):
                tags[sid].append("📐 法則2")
            if bool(sell.get(sid, False)):
                tags[sid].append("⚠️ 葛蘭碧賣訊")
    return tags
