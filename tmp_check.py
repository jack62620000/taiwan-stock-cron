"""暫時測試：千張大戶回補修正版（不寫資料庫）"""
import time
import fetch_stock_info as f
import fetch_holder_history as h
f.save_data_batches = lambda t, recs: (print("  [試跑不寫入]", t, len(recs), recs[:2]) or (len(recs), 0))
t0 = time.time(); d = h.delisted_dates(); print("下市", len(d), list(d.items())[:3], f"{time.time()-t0:.1f}s")
t0 = time.time(); print("已存", len(h.stocks_already_saved("2026-10-08")), f"{time.time()-t0:.1f}s")
some = {"2330", "2317", "2454", "8069", "6488"} | set(list(d)[:3])
print(h.fetch_range("2025-10-17", "2025-10-24", some))
