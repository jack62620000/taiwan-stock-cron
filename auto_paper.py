"""
照規則自動模擬 - 每晚評分完，照「明日買進」規則自動記錄模擬買賣（規則見 app/utils/autotrade.py）

紀錄寫進環境變數 AUTO_PAPER_GROUP_ID 指定的分組（網頁「模擬持股」頁選那個分組就能看）；沒設定就不做事。
分組建立當天之後的交易日才開始買賣（第一筆是建立當天的清單、隔天開盤買）。
每次從上次買賣的日期之後接著做，漏跑幾天也會一天一天補上；同一天重跑不會重複記錄。
資料量很小：一年約 250 筆買進紀錄。
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app"))

from utils.analysis import MARKET_MA  # noqa: E402
from utils.autotrade import run_rule  # noqa: E402
from utils.db import (  # noqa: E402
    add_paper_trades, get_group, get_market_index, get_paper_trades, get_rows_for_stocks,
    get_signal_dates, get_top_ranks, sell_paper_trade,
)
from utils.picks import TOP_N, market_ok_by_date  # noqa: E402

TW = timezone(timedelta(hours=8))


def tw_date(ts):
    """資料庫的時間（UTC）換成台灣日期"""
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(TW).date().isoformat()


def main():
    group_id = os.getenv("AUTO_PAPER_GROUP_ID", "").strip()
    if not group_id:
        print("沒有設定 AUTO_PAPER_GROUP_ID，不記錄")
        return 0
    group = get_group(int(group_id))
    if group is None:
        print(f"找不到分組 {group_id}")
        return 1
    print(f"分組：{group['name']}")
    gid, start = group["id"], tw_date(group["created_at"])
    trades = get_paper_trades(gid)
    rows = trades.astype(object).where(trades.notna(), None).to_dict("records") if not trades.empty else []
    last = max([start] + [str(t["buy_date"]) for t in rows] + [str(t["sell_date"]) for t in rows if t.get("sell_date")])

    # 從上次買賣日（和持有中最早的買進日）前一個交易日開始：要用前一天的排名、算持有幾天
    all_dates = sorted(get_signal_dates(n=120))
    held_since = min([str(t["buy_date"]) for t in rows if not t.get("sell_date")] or [last])
    first = max(sum(d < min(last, held_since) for d in all_dates) - 1, 0)
    dates = all_dates[first:]
    if not dates or dates[-1] <= last:
        print(f"{last} 之後還沒有新的交易日")
        return 0

    ranks_df = get_top_ranks(dates[0], TOP_N)
    ranks = {d: list(g.sort_values("rank")["stock_id"]) for d, g in ranks_df.groupby("date")} if not ranks_df.empty else {}
    index = get_market_index("TAIEX", limit=MARKET_MA + len(dates) + 10)
    market_ok = {d: v for d, v in market_ok_by_date(index).items() if d in set(dates)}

    ids = sorted({s for v in ranks.values() for s in v} | {t["stock_id"] for t in rows if not t.get("sell_date")})
    px = get_rows_for_stocks("daily_prices", ids, since=dates[0], columns="stock_id, date, open_price, close_price")
    opens, closes = {}, {}
    if not px.empty:
        for c in ("open_price", "close_price"):
            px[c] = pd.to_numeric(px[c], errors="coerce")
        opens = {(r.stock_id, r.date): r.open_price for r in px.itertuples()}
        closes = {(r.stock_id, r.date): r.close_price for r in px.itertuples()}

    sells, buys, done = run_rule(rows, dates, start, ranks, market_ok, opens, closes)
    for trade_id, day, price in sells:
        sell_paper_trade(int(trade_id), day, round(float(price), 2))
    add_paper_trades(gid, [{**b, "buy_price": round(float(b["buy_price"]), 2),
                            "sell_price": None if b["sell_price"] is None else round(float(b["sell_price"]), 2)}
                           for b in buys])
    for trade_id, day, price in sells:
        print(f"{day} 開盤賣出 id={trade_id} {price}")
    for b in buys:
        print(f"{b['buy_date']} 開盤買進 {b['stock_id']} {b['buy_price']} × {b['shares'] * 1000:.0f} 股（{b['note']}）")
    print(f"處理到 {done or '（沒有新的交易日）'}：賣出 {len(sells)} 筆、買進 {len(buys)} 筆")
    return 0


if __name__ == "__main__":
    sys.exit(main())
