"""暫時測試：停牌／無成交處理（只讀不寫），用 Streamlit AppTest 實際跑頁面，測完會刪除"""
import sys, time
sys.path.insert(0, "app")
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from utils import db
from utils.indicators import calc_all, calc_ma
db.ThreadPoolExecutor = lambda max_workers=1: ThreadPoolExecutor(max_workers=1)  # Actions 上平行讀取會斷線

for sid in ("2321", "5906", "1423"):
    p = db.get_daily_prices(sid, limit=250)
    old = calc_ma(p.copy().assign(close_price=pd.to_numeric(p["close_price"])))
    new = calc_all(p.copy())
    print(f"{sid}: 無成交 {int(new['no_trade'].sum())} 天，MA20 算得出來的天數 舊 {old['MA20'].notna().sum()} → 新 {new['MA20'].notna().sum()}，"
          f"最新 {new['date'].iloc[-1]} 收 {new['close_price'].iloc[-1]} MA20 {new['MA20'].iloc[-1]} RSI {new['RSI'].iloc[-1]}")

from streamlit.testing.v1 import AppTest
def run(page, stock=None):
    t = time.time()
    at = AppTest.from_file(f"app/pages/{page}", default_timeout=600)
    at.session_state["authenticated"] = True
    if stock:
        at.session_state["card_stock_id"] = stock
    at.run()
    errs = [e.value for e in at.exception]
    print(f"[{page}] {time.time()-t:.0f}s 例外 {len(errs)}", errs[:1])
    for w in at.warning:
        print("   warning:", w.value[:120])
    for c in at.caption:
        if "⏸️" in c.value:
            print("   caption:", c.value[:120])
    return at

for sid in ("2321", "6669"):
    print("個股", sid)
    run("2_個股分析卡.py", sid)
run("1_總覽.py")
run("6_葛蘭碧八大法則.py")
