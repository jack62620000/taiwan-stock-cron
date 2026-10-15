"""
葛蘭碧紙上交易 - 給 GitHub Actions 每日排程使用

從 PAPER_START 那天起，每個交易日依固定條件模擬買賣，結果寫進 granville_paper_trades 表，
網頁「葛蘭碧八大法則 → 模擬交易」分頁會讀這張表顯示持倉與績效。

做法：每次都用 PAPER_START 到最新交易日的股價，從頭跑一次 app/utils/granville.py 的
simulate_trades()（和網頁歷史回測同一套規則），再把結果 upsert 進資料表。
同一天重跑結果一定一樣，漏跑幾天也會自動補上。

狀態：
  pending：今天收盤出現買進訊號，明天開盤才買
  open：持有中（return_pct 是用最新收盤價算的未實現報酬）
  closed：已出場

用法：
    python paper_trade.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.append(os.path.join(os.path.dirname(__file__), "app"))
from utils.granville import rule_frames, simulate_trades, _pivot  # noqa: E402
from signals import _client, _execute, _error, load_daily, trading_dates  # noqa: E402

# ===== 紙上交易的固定條件（和網頁模擬交易分頁的預設值相同）=====
PAPER_START = "2026-10-15"   # 從這天的收盤訊號開始記錄
BUY_RULES = [2]
SELL_RULES = [6, 7]
HOLD_DAYS = 20
STOP_LOSS_PCT = 8.0
MIN_VOLUME = 500             # 20 日均量下限（張）
RULE_PARAMS = dict(ma_days=20, slope_days=5, lookback=5, near_pct=2.0, bias_pct=10.0)
WARMUP_DAYS = 70             # 起點之前多載入的交易日，夠算季線與均線方向


def _clean(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return v


def build_rows(prices, start=PAPER_START):
    """依股價算出要寫進 granville_paper_trades 的資料列"""
    trades, _ = simulate_trades(
        prices, BUY_RULES, SELL_RULES, hold_days=HOLD_DAYS, stop_loss_pct=STOP_LOSS_PCT,
        min_volume=MIN_VOLUME, start_date=start, **RULE_PARAMS,
    )
    rows = []
    held = set()
    for t in trades.to_dict("records"):
        is_open = t["exit_reason"] == "未平倉"
        if is_open:
            held.add(t["stock_id"])
        rows.append({
            "stock_id": t["stock_id"],
            "signal_date": t["signal_date"],
            "entry_date": t["entry_date"],
            "entry_price": round(t["entry_price"], 4),
            "exit_date": None if is_open else t["exit_date"],
            "exit_price": None if is_open else round(t["exit_price"], 4),
            "exit_reason": None if is_open else t["exit_reason"],
            "status": "open" if is_open else "closed",
            "return_pct": round(t["return_pct"], 4),
            "rules": [int(n) for n in t["rules"]],
        })

    # 最新一天收盤出現買進訊號、還沒持有的 → 明天開盤買（pending）
    close = _pivot(prices, "close_price")
    last = close.index[-1]
    if last >= start:
        hits = rule_frames(close, **RULE_PARAMS)["hits"]
        vol = _pivot(prices, "volume").reindex(index=close.index, columns=close.columns)
        avg_vol = (vol.fillna(0).rolling(20, min_periods=20).mean() / 1000).iloc[-1]
        for sid in close.columns:
            fired = [n for n in BUY_RULES if bool(hits[n][sid].iloc[-1])]
            if fired and sid not in held and _clean(avg_vol.get(sid)) is not None \
                    and avg_vol[sid] >= MIN_VOLUME:
                rows.append({
                    "stock_id": sid, "signal_date": last, "entry_date": None, "entry_price": None,
                    "exit_date": None, "exit_price": None, "exit_reason": None,
                    "status": "pending", "return_pct": None, "rules": fired,
                })
    return [{k: _clean(v) for k, v in r.items()} for r in rows]


def main():
    sb = _client()
    dates = trading_dates(sb)
    if not dates or dates[-1] < PAPER_START:
        print(f"最新交易日 {dates[-1] if dates else '無'} 早於起始日 {PAPER_START}，還不用跑")
        return 0
    first = max(0, next(i for i, d in enumerate(dates) if d >= PAPER_START) - WARMUP_DAYS)
    load = dates[first:]
    print(f"載入 {load[0]} ～ {load[-1]} 共 {len(load)} 天股價…")
    prices = load_daily(sb, "daily_prices", "stock_id, date, open_price, close_price, volume", load)
    rows = build_rows(prices)
    print(f"紙上交易 {len(rows)} 筆：" + "、".join(
        f"{s} {sum(r['status'] == s for r in rows)} 筆" for s in ("pending", "open", "closed")))
    if rows:
        _execute(lambda: sb.table("granville_paper_trades").upsert(rows, on_conflict="stock_id,signal_date"))
    # 之前的 pending 若隔天沒買成（例如停牌），上面不會把它轉成 open，這裡清掉
    _execute(lambda: sb.table("granville_paper_trades").delete()
             .eq("status", "pending").lt("signal_date", load[-1]))
    print("✅ 紙上交易更新完成")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        tb = traceback.format_exc()
        print(tb)
        _error("paper_trade.py 執行失敗：" + tb.strip().splitlines()[-1])
        sys.exit(1)
