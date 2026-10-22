"""
動能選股 - 給 GitHub Actions 排程使用（每日評分之後執行），也可以手動回補

每個交易日收盤後，幫全市場普通股檢查兩種動能條件，寫進 Supabase 的 momentum_signals 表：
  1. 趨勢模板（Mark Minervini）：下面 8 個條件全部符合
     ① 收盤在 150 日、200 日均線之上        ② 150 日均線在 200 日均線之上
     ③ 200 日均線比 20 個交易日前高（上揚至少一個月）
     ④ 50 日均線在 150 日、200 日均線之上   ⑤ 收盤在 50 日均線之上
     ⑥ 收盤比 52 週低點高 30% 以上          ⑦ 收盤在 52 週高點的 25% 以內
     ⑧ 相對強度（RS）在有流動性的股票裡前 30%（RS 是近 3、6、9、12 個月漲幅的加權，最近 3 個月占 40%）
  2. 52 週新高突破：收盤創 52 週新高，而且成交量至少是前 50 日均量的 1.5 倍；
     前 10 個交易日內已經突破過的不重複算。
  3. 趨勢中回檔再起：符合趨勢模板，前 10 個交易日內收盤碰過布林下軌（20 日均線 − 2 個標準差），
     今天收盤由下往上站上布林中軌（20 日均線）。
  只有 20 日均量 ≥ 500 張的股票會判斷這兩種條件；其他股票仍寫入均線等數值給個股分析卡參考。

回測（2025/1～2026/10，20 日均量 500 張以上的普通股，訊號隔天開盤買進，未扣約 0.6% 交易成本）：
  全部股票      20 日平均 +1.8%、60 日平均 +7.0%、60 日勝率 49%
  趨勢模板      +4.3%、+14.7%、52%
  新高突破＋量增 +3.9%、+12.5%、51%
  兩者同時      +5.2%、+17.2%、55%
  2025、2026 兩年分開看都贏全部股票；勝率只有五成上下，報酬集中在少數大漲股，要搭配停損。
  和全部股票平均比的 60 日超額報酬：趨勢模板 +5.6%、趨勢中回檔再起 +11.0%（約 350 次）；
  離 50 日均線 30% 以上的趨勢模板股只剩 +0.2%，網頁與 LINE 的清單會剔除（utils.momentum.HOT_PCT）。
  這段期間台股偏多頭，空頭時效果可能變差。

用法：
    python momentum.py                                  # 計算資料庫裡最新的交易日
    python momentum.py --start 2026-10-01               # 從 10/1 回補到最新交易日
    python momentum.py --start 2026-10-01 --end 2026-10-15
也可以用環境變數 MOMENTUM_START / MOMENTUM_END（給 GitHub Actions 手動執行用）。
技術指標用還原權值後的股價計算；寫進資料表的 close_price 是原始收盤價。
"""
import argparse
import math
import os
import sys

import numpy as np
import pandas as pd

from signals import _client, _error, _execute, _fetch_all, _pivot, apply_adjustments, load_daily, trading_dates

LOOKBACK_DAYS = 275       # 計算日之前要多載入的交易日數（52 週高點、RS 的 12 個月漲幅、200 日均線 20 天前的值）
LIQUID_MIN_SHARES = 500_000   # 20 日均量至少 500 張才判斷動能條件
RS_MIN = 70               # 相對強度至少贏過 70% 的股票
BREAKOUT_VOL = 1.5        # 突破當天成交量至少是前 50 日均量的倍數
BREAKOUT_GAP = 10         # 前幾個交易日內已突破過就不重複算
ATR_DAYS = 14
BOLL_DAYS = 20            # 布林通道用 20 日均線 ± 2 個標準差
PULLBACK_DAYS = 10        # 趨勢中回檔再起：前幾個交易日內碰過布林下軌
KEEP_DAYS = 5             # 資料表只保留最近這麼多個交易日（網頁只看最新一天；資料庫免費額度 500 MB，盡量省空間）
EVENT_KEEP_DAYS = 30      # 有訊號的列（新高突破、剛符合趨勢模板）多留這麼多個交易日，給每日選股頁的事後追蹤（每天約十幾～幾十列）
BATCH_SIZE = 500
STOCK_PATTERN = r"^[1-9]\d{3}$"   # 一般股票（排除 ETF、權證）


def compute(prices, adjust, targets):
    """
    prices：stock_id、date、high_price、low_price、close_price、volume（原始價格），
    要包含 targets 之前至少 LOOKBACK_DAYS 個交易日；adjust：還原權值事件。
    回傳 targets 每一天、每檔股票一列的 dict。
    """
    prices = prices[prices["stock_id"].astype(str).str.match(STOCK_PATTERN)]
    if prices.empty:
        return []
    close_raw = _pivot(prices, "close_price")
    adj = apply_adjustments(prices, adjust)
    close = _pivot(adj, "close_price").ffill(limit=5)
    # 最高、最低價用和收盤價相同的還原比例
    mult = (_pivot(adj, "close_price") / close_raw).ffill()
    high = (_pivot(prices, "high_price") * mult).reindex_like(close)
    low = (_pivot(prices, "low_price") * mult).reindex_like(close)
    vol = _pivot(adj, "volume").reindex_like(close).fillna(0)

    ma50 = close.rolling(50, min_periods=50).mean()
    ma150 = close.rolling(150, min_periods=150).mean()
    ma200 = close.rolling(200, min_periods=200).mean()
    ma200_prev = ma200.shift(20)
    high52 = close.rolling(252, min_periods=252).max()
    low52 = close.rolling(252, min_periods=252).min()
    high52_prev = high52.shift(1)
    vol50_prev = vol.rolling(50, min_periods=50).mean().shift(1)
    vol20 = vol.rolling(20, min_periods=20).mean()
    liquid = vol20 >= LIQUID_MIN_SHARES

    prev_close = close.shift(1)
    tr = np.maximum(np.maximum(high - low, (high - prev_close).abs()), (low - prev_close).abs())
    atr = tr.rolling(ATR_DAYS, min_periods=ATR_DAYS).mean()

    rs_raw = sum(w * close / close.shift(n) for w, n in ((0.4, 63), (0.2, 126), (0.2, 189), (0.2, 252)))
    rs = rs_raw.where(liquid).rank(axis=1, pct=True)
    rs_rating = (rs * 100).clip(upper=99).apply(np.floor)

    checks = [
        (close > ma150) & (close > ma200),
        ma150 > ma200,
        ma200 > ma200_prev,
        (ma50 > ma150) & (ma50 > ma200),
        close > ma50,
        close >= low52 * 1.3,
        close >= high52 * 0.75,
        rs_rating >= RS_MIN,
    ]
    n_checks = sum(c.astype(int) for c in checks)
    template = (n_checks == len(checks)) & liquid
    template_new = template & ~template.shift(1, fill_value=False)
    vol_ratio = vol / vol50_prev
    new_high = close > high52_prev
    raw_break = new_high & (vol_ratio >= BREAKOUT_VOL) & liquid
    recent = raw_break.astype(float).shift(1).rolling(BREAKOUT_GAP, min_periods=1).max().fillna(0)
    breakout = raw_break & (recent == 0)
    mid = close.rolling(BOLL_DAYS, min_periods=BOLL_DAYS).mean()
    lower = mid - 2 * close.rolling(BOLL_DAYS, min_periods=BOLL_DAYS).std(ddof=0)
    touched = (close <= lower).astype(float).shift(1).rolling(PULLBACK_DAYS, min_periods=1).max().fillna(0) > 0
    pullback = template & touched & (close > mid) & (close.shift(1) <= mid.shift(1))

    def num(frame, d, sid, digits=2):
        v = frame.at[d, sid] if sid in frame.columns else np.nan
        # 前 50 日都沒成交的股票量比會是無限大，JSON 存不進去，和缺值一樣留空
        return None if v is None or not math.isfinite(float(v)) else round(float(v), digits)

    rows = []
    for d in targets:
        if d not in close_raw.index:
            continue
        for sid in close_raw.columns[close_raw.loc[d].notna()]:
            rs_v = num(rs_rating, d, sid, 0)
            rows.append({
                "stock_id": sid, "date": d,
                "close_price": num(close_raw, d, sid),
                "ma50": num(ma50, d, sid), "ma150": num(ma150, d, sid),
                "ma200": num(ma200, d, sid), "ma200_prev": num(ma200_prev, d, sid),
                "high_52w": num(high52, d, sid), "low_52w": num(low52, d, sid),
                "rs_rating": int(rs_v) if rs_v is not None else None,
                "vol_ratio": num(vol_ratio, d, sid),
                "atr14": num(atr, d, sid, 3),
                "trend_checks": int(n_checks.at[d, sid]),
                "trend_template": bool(template.at[d, sid]),
                "trend_new": bool(template_new.at[d, sid]),
                "new_high": bool(new_high.at[d, sid]),
                "breakout": bool(breakout.at[d, sid]),
                "pullback": bool(pullback.at[d, sid]),
                "liquid": bool(liquid.at[d, sid]),
            })
    return rows


def load_inputs(sb, start=None, end=None):
    all_dates = trading_dates(sb, end)
    if not all_dates:
        return [], pd.DataFrame(), pd.DataFrame(), []
    end = end or all_dates[-1]
    start = start or end
    targets = [d for d in all_dates if start <= d <= end]
    if not targets:
        return [], pd.DataFrame(), pd.DataFrame(), all_dates
    first_idx = all_dates.index(targets[0])
    dates = all_dates[max(0, first_idx - LOOKBACK_DAYS): all_dates.index(targets[-1]) + 1]
    print(f"計算 {len(targets)} 個交易日（{targets[0]} ～ {targets[-1]}），載入 {len(dates)} 天的股價…")
    prices = load_daily(sb, "daily_prices", "stock_id, date, high_price, low_price, close_price, volume", dates)
    adjust = pd.DataFrame(_fetch_all(
        lambda: sb.table("price_adjustments").select("stock_id, date, event_type, factor")
            .gt("date", dates[0]).lte("date", targets[-1])
            .order("date", desc=False).order("stock_id", desc=False).order("event_type", desc=False)))
    print(f"  股價 {len(prices):,} 筆、還原權值事件 {len(adjust):,} 筆")
    return targets, prices, adjust, all_dates


def save(sb, rows):
    failed = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        try:
            _execute(lambda: sb.table("momentum_signals").upsert(batch))
        except Exception as e:
            failed += len(batch)
            _error(f"寫入 momentum_signals 失敗（{rows[i]['date']}，Index {i}）：{e}")
    return failed


def prune(sb, all_dates):
    """
    全部股票只保留最近 KEEP_DAYS 個交易日；更早的只留有訊號的列（20 日均量夠，而且新高突破、剛符合趨勢模板或趨勢中回檔再起），
    保留到 EVENT_KEEP_DAYS 個交易日，再更早的全部刪除
    """
    table = lambda: sb.table("momentum_signals").delete(returning="minimal")  # noqa: E731
    if len(all_dates) > EVENT_KEEP_DAYS:
        cutoff = all_dates[-EVENT_KEEP_DAYS]
        _execute(lambda: table().lt("date", cutoff))
        print(f"  已刪除 {cutoff} 以前的資料")
    if len(all_dates) > KEEP_DAYS:
        cutoff = all_dates[-KEEP_DAYS]
        _execute(lambda: table().lt("date", cutoff).eq("liquid", False))
        _execute(lambda: table().lt("date", cutoff).eq("breakout", False).eq("trend_new", False).eq("pullback", False))
        print(f"  {cutoff} 以前只保留有訊號的列")


def main():
    parser = argparse.ArgumentParser(description="動能選股（趨勢模板、52 週新高突破）")
    parser.add_argument("--start", default=os.getenv("MOMENTUM_START", "").strip() or None)
    parser.add_argument("--end", default=os.getenv("MOMENTUM_END", "").strip() or None)
    args = parser.parse_args()

    sb = _client()
    targets, prices, adjust, all_dates = load_inputs(sb, args.start, args.end)
    if not targets:
        _error("找不到要計算的交易日")
        return 1
    rows = compute(prices, adjust, targets)
    failed = 0
    for d in targets:
        day = [r for r in rows if r["date"] == d]
        if not day:
            print(f"  {d}：沒有資料，略過")
            continue
        failed += save(sb, day)
        print(f"  {d}：{len(day)} 檔，趨勢模板 {sum(r['trend_template'] for r in day)} 檔"
              f"（剛進入 {sum(r['trend_new'] for r in day)}）、新高突破 {sum(r['breakout'] for r in day)} 檔、"
              f"趨勢中回檔再起 {sum(r['pullback'] for r in day)} 檔")

    try:
        prune(sb, all_dates)
    except Exception as e:
        _error(f"刪除舊資料失敗（今天的結果已寫入）：{e}")
        failed += 1

    if failed:
        _error(f"有 {failed} 筆寫入失敗")
        return 1
    print("✅ 動能選股完成")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        tb = traceback.format_exc()
        print(tb)
        if os.getenv("GITHUB_ACTIONS"):
            print("::error title=momentum.py 執行失敗::" + tb.strip().replace("%", "%25").replace("\n", "%0A"))
        sys.exit(1)
