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
**規則**
1. 看大盤：加權指數收盤在 60 日均線（季線）之上才買；在季線下就不買新股票。
2. 買進：隔天開盤買每日選股前 {TOP_N} 名。
3. 賣出：買進後第 {HOLD} 個交易日開盤賣出，不管賺賠。
4. 回測的做法是最多同時持有 {SLOTS} 檔、每檔約放總資金的 1/{SLOTS}，名額滿了就不買、已持有的不重複買。

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


def pick_record(dates, ranks, market_ok, prices, raw=None, hold=HOLD, cost_pct=0.0):
    """
    每個評分日前 N 名的紀錄：評分隔天開盤買、第 hold 個交易日開盤賣的報酬（%，扣交易成本）。
    dates：評分日（交易日），由舊到新；ranks：date、stock_id、rank、total_score；
    market_ok：{評分日: 大盤在季線上}；prices：stock_id、date、open_price、close_price（還原權值，算報酬）；
    raw：同樣欄位的原始價格（顯示買進價、賣出價、最新收盤，和券商看到的一樣），沒給就用 prices。
    還沒到期的用最新收盤價算「至今」報酬。最新評分日還沒買進，不列入。
    """
    cols = ["signal_date", "rank", "stock_id", "total_score", "market_ok", "buy_date", "sell_date",
            "status", "buy_price", "sell_price", "last_close", "ret"]
    if ranks.empty or len(dates) < 2:
        return pd.DataFrame(columns=cols)
    pos = {d: i for i, d in enumerate(dates)}
    def lookup(df):
        df = df.copy()
        for c in ["open_price", "close_price"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return (df.set_index(["stock_id", "date"])["open_price"],
                df.dropna(subset=["close_price"]).sort_values("date").groupby("stock_id")["close_price"].last())

    opens, last_close = lookup(prices)
    raw_opens, raw_close = lookup(raw if raw is not None else prices)
    rows = []
    for r in ranks.itertuples():
        i = pos.get(r.date)
        if i is None or i + 1 >= len(dates):
            continue
        buy_date = dates[i + 1]
        sell_i = i + 1 + hold
        sold = sell_i < len(dates)
        buy = opens.get((r.stock_id, buy_date))
        now = opens.get((r.stock_id, dates[sell_i])) if sold else last_close.get(r.stock_id)
        rows.append({
            "signal_date": r.date, "rank": r.rank, "stock_id": r.stock_id, "total_score": r.total_score,
            "market_ok": bool(market_ok.get(r.date)), "buy_date": buy_date,
            "sell_date": dates[sell_i] if sold else None,
            "status": "已賣出" if sold else f"持有第 {len(dates) - 1 - i} / {hold} 天",
            "buy_price": raw_opens.get((r.stock_id, buy_date)),
            "sell_price": raw_opens.get((r.stock_id, dates[sell_i])) if sold else None,
            "last_close": None if sold else raw_close.get(r.stock_id),
            "ret": (now / buy - 1) * 100 - cost_pct if buy and now and pd.notna(buy) and pd.notna(now) else None,
        })
    df = pd.DataFrame(rows, columns=cols)
    for c in ["buy_price", "sell_price", "last_close", "ret"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values(["signal_date", "rank"], ascending=[False, True]).reset_index(drop=True)


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


def buy_list():
    """
    明日買進清單（網頁選股頁與 LINE 共用）：最新評分日前 TOP_N 名，加上名稱、產業與參考標籤。
    回傳 (評分日, DataFrame)；沒有資料時是 (None, 空表)
    """
    from datetime import date, timedelta
    from utils.db import (
        apply_adjustments, get_momentum_latest, get_price_adjustments, get_rows_for_stocks, get_signal_dates,
        get_stock_list, get_top_signals,
    )
    dates = get_signal_dates(n=1)
    if not dates:
        return None, pd.DataFrame()
    top = get_top_signals(dates[0], TOP_N)
    if top.empty:
        return dates[0], top
    ids = sorted(top["stock_id"])
    since = (date.today() - timedelta(days=200)).isoformat()
    prices = get_rows_for_stocks("daily_prices", ids, since=since, columns="stock_id, date, close_price")
    if not prices.empty:
        prices = apply_adjustments(prices, get_price_adjustments(None, since))
    tags = stock_tags(ids, prices, get_momentum_latest())
    info = get_stock_list(include_delisted=True)
    if not info.empty:
        top = top.merge(info[["stock_id", "stock_name", "industry"]], on="stock_id", how="left")
    else:
        top = top.assign(stock_name="", industry="")
    top["tags"] = top["stock_id"].map(lambda s: tags.get(s, []))
    return dates[0], top.sort_values("rank").reset_index(drop=True)
