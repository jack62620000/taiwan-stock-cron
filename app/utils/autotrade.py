"""
照規則自動模擬：每晚依「明日買進」規則自動記錄模擬買賣（auto_paper.py 寫入，在「模擬持股」頁選那個分組就能看）

規則和回測相同：資金分 SLOTS 格，每格是前一天收盤總資產的 1/SLOTS；
前一個評分日大盤在月線和季線之上時，有空格就照排名由上往下，買前 TOP_N 名裡還沒持有的股票（當天開盤價）；
買進後第 HOLD 個交易日開盤賣出。價格用原始開盤價（和券商看到的一樣），股數取整數股（可以是零股）。
紀錄放在 paper_trades 指定的分組（GitHub Actions 設定 AUTO_PAPER_GROUP_ID）；分組裡的每一筆都算在這個帳戶裡。
"""
import math

import pandas as pd

from utils.paper import BUY_FEE, SELL_FEE, SELL_TAX
from utils.picks import HOLD, SLOTS

SOURCE = "照規則自動"
START_CASH = 1_000_000      # 起始資金（元）


def _ok(v):
    return v is not None and not pd.isna(v) and v > 0


def run_rule(trades, dates, start, ranks, market_ok, opens, closes,
             hold=HOLD, slots=SLOTS, start_cash=START_CASH):
    """
    從上次處理到的交易日往後，一天一天照規則買賣。
    trades：已記錄的交易（dict：id、stock_id、buy_date、buy_price、shares（張）、sell_date、sell_price）；
    dates：交易日（評分日），由舊到新；start：開始記錄的日期（這天之後的交易日才買賣）；
    ranks：{評分日: [前 N 名代號，依排名]}；market_ok：{評分日: 大盤在月線和季線之上}；
    opens、closes：{(代號, 日期): 原始開盤價／收盤價}。
    回傳 (賣出 [(id, 日期, 價格)], 買進 [dict], 處理到的最後一天)。
    前一個評分日的排名或大盤資料還沒有、當天還沒有任何開盤價時就停下來，等下次資料齊了再接著做。
    """
    pos = {d: i for i, d in enumerate(dates)}
    cash = float(start_cash)
    held = []    # 持有中：{id, stock_id, buy_date, price, qty}
    last = start
    for t in trades:
        qty = round(float(t["shares"]) * 1000)
        cash -= float(t["buy_price"]) * qty * (1 + BUY_FEE)
        last = max(last, str(t["buy_date"]))
        if t.get("sell_date") and t.get("sell_price") is not None:
            cash += float(t["sell_price"]) * qty * (1 - SELL_FEE - SELL_TAX)
            last = max(last, str(t["sell_date"]))
        else:
            held.append({"id": t.get("id"), "stock_id": t["stock_id"], "buy_date": str(t["buy_date"]),
                         "price": float(t["buy_price"]), "qty": qty})

    sells, buys, done = [], [], None
    day_opens = {}
    for (sid, d), v in opens.items():
        if _ok(v):
            day_opens.setdefault(d, set()).add(sid)
    for i, day in enumerate(dates):
        if day <= last or i == 0:
            continue
        prev = dates[i - 1]
        if prev not in ranks or prev not in market_ok or day not in day_opens:
            break
        # 前一天收盤的總資產（每格金額用這個算，和回測相同）
        value = sum(h["qty"] * (closes.get((h["stock_id"], prev)) if _ok(closes.get((h["stock_id"], prev)))
                                else h["price"]) for h in held)
        equity = cash + value
        # 1. 到期的開盤賣出（停牌沒有開盤價就等下一天）
        for h in list(held):
            px = opens.get((h["stock_id"], day))
            if h["buy_date"] in pos and pos[day] - pos[h["buy_date"]] >= hold and _ok(px):
                cash += px * h["qty"] * (1 - SELL_FEE - SELL_TAX)
                if h["id"] is not None:
                    sells.append((h["id"], day, px))
                else:   # 這次才買進、這次就到期（補做好幾天時）
                    next(b for b in buys if b["_key"] == h["key"]).update(sell_date=day, sell_price=px)
                held.remove(h)
        # 2. 有空格、前一天大盤在月線和季線上，就照排名買
        free = slots - len(held)
        if free > 0 and market_ok[prev]:
            have = {h["stock_id"] for h in held}
            for sid in [s for s in ranks[prev] if s not in have and _ok(opens.get((s, day)))][:free]:
                px = opens[(sid, day)]
                qty = math.floor(min(cash, equity / slots) / (px * (1 + BUY_FEE)))
                if qty < 1:
                    continue
                cash -= px * qty * (1 + BUY_FEE)
                key = (sid, day)
                rank = ranks[prev].index(sid) + 1
                buys.append({"_key": key, "stock_id": sid, "source": SOURCE, "buy_date": day, "buy_price": px,
                             "shares": qty / 1000, "sell_date": None, "sell_price": None,
                             "note": f"{prev} 評分第 {rank} 名"})
                held.append({"id": None, "key": key, "stock_id": sid, "buy_date": day, "price": px, "qty": qty})
        done = day
    for b in buys:
        b.pop("_key")
    return sells, buys, done

