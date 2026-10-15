"""暫時測試：還原權值後的股價讀取（只讀不寫），測完會刪除"""
import sys, time
sys.path.insert(0, "app")
import pandas as pd
from utils import db
from concurrent.futures import ThreadPoolExecutor
# GitHub Actions 上多執行緒共用連線會被斷線（網頁本身不受影響），測試時改成逐日依序讀
db.ThreadPoolExecutor = lambda max_workers=1: ThreadPoolExecutor(max_workers=1)
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

for days, cols in ((40, "stock_id, date, close_price"), (100, "stock_id, date, close_price, volume")):
    t = time.time()
    u = db.get_recent_closes(calendar_days=days, columns=cols, adjust=False)
    t_raw = time.time() - t
    t = time.time()
    ev = db.get_price_adjustments(None, u["date"].min())
    t_ev = time.time() - t
    t = time.time()
    r = db.apply_adjustments(u, ev)
    t_apply = time.time() - t
    m = r.merge(u, on=["stock_id", "date"], suffixes=("", "_raw")).sort_values(["stock_id", "date"])
    changed = (m["close_price"].round(2) != pd.to_numeric(m["close_price_raw"]).round(2)).sum()
    for c in ("close_price_raw", "close_price"):
        m[c + "_chg"] = m.groupby("stock_id")[c].transform(lambda s: pd.to_numeric(s).pct_change().abs())
    print(f"全市場 {days} 天：{len(r)} 筆，讀股價 {t_raw:.1f}s、讀事件 {len(ev)} 筆 {t_ev:.1f}s、換算 {t_apply:.2f}s，調整到的列 {changed}，"
          f"單日變動超過 11% 的次數 還原前 {(m['close_price_raw_chg'] > 0.11).sum()} → 還原後 {(m['close_price_chg'] > 0.11).sum()}")
    print(m[m["close_price_chg"] > 0.11][["stock_id", "date", "close_price_raw", "close_price", "close_price_chg"]].to_string())
