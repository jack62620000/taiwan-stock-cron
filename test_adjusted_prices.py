"""暫時測試：還原權值後的股價讀取（只讀不寫），測完會刪除"""
import sys, time
sys.path.insert(0, "app")
import pandas as pd
from utils import db
from utils.indicators import calc_all

for sid in ("6669", "4747", "6949", "2380", "2330"):
    t = time.time()
    p = db.get_daily_prices(sid, limit=250)
    raw = db.get_daily_prices(sid, limit=250, adjust=False)
    d = pd.to_numeric(p["close_price"]).pct_change().abs()
    draw = pd.to_numeric(raw["close_price"]).pct_change().abs()
    print(f"{sid}: {round(time.time()-t,2)}s 筆數 {len(p)}，最大單日變動 還原前 {draw.max():.1%} → 還原後 {d.max():.1%}，"
          f"最新收盤 {raw['close_price'].iloc[-1]} / {p['close_price'].iloc[-1]}")
    ta = calc_all(p.copy())
    print("   ", ta[["date", "close_price", "MA20", "RSI"]].tail(1).to_string(header=False, index=False) if "RSI" in ta else ta.columns.tolist())

for days, cols in ((40, "stock_id, date, close_price"), (100, "stock_id, date, close_price, volume"),
                   (465, "stock_id, date, open_price, close_price, volume")):
    t = time.time()
    r = db.get_recent_closes(calendar_days=days, columns=cols)
    t1 = time.time() - t
    t = time.time()
    u = db.get_recent_closes(calendar_days=days, columns=cols, adjust=False)
    t2 = time.time() - t
    m = r.merge(u, on=["stock_id", "date"], suffixes=("", "_raw"))
    changed = (m["close_price"].round(2) != pd.to_numeric(m["close_price_raw"]).round(2)).sum()
    m = m.sort_values(["stock_id", "date"])
    jump = lambda c: m.groupby("stock_id")[c].apply(lambda s: pd.to_numeric(s).pct_change().abs().gt(0.11).sum()).sum()
    print(f"全市場 {days} 天：{len(r)} 筆，還原 {t1:.1f}s / 原始 {t2:.1f}s，調整到的列 {changed}，"
          f"單日變動超過 11% 的次數 還原前 {jump('close_price_raw')} → 還原後 {jump('close_price')}")
