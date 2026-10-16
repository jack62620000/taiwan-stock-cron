"""暫時測試：千張大戶回補（不寫資料庫）"""
import time
import fetch_stock_info as f
import fetch_holder_history as h
f.save_data_batches = lambda t, recs: (print("  [試跑不寫入]", t, len(recs), recs[:3]) or (len(recs), 0))
valid = f.get_valid_stock_ids()
print("valid", len(valid))
for w in ["2026-10-08", "2025-10-17"]:
    near, saved = h.stocks_traded_near(w), h.stocks_already_saved(w)
    print(w, "交易", len(near), "已存", len(saved), "需補", len((near & valid) - saved))
print(h.fetch_range("2025-10-17", "2025-10-24", {"2330", "2317", "2454", "8069", "6488", "9999"}))
print(h.fetch_range("2026-10-08", "2026-10-08", valid, deadline=time.time() + 600))
