import importlib.util, sys, time
sys.path.insert(0, "app")
import pandas as pd
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
from utils.db import get_recent_closes, get_recent_ins
from utils.analysis import GRANVILLE_DEFAULTS, granville_followup, momentum_followup, followup_summary
# 觀察名單重構前後相同
spec = importlib.util.spec_from_file_location("old_granville", "base/app/utils/granville.py")
old = importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
import utils.granville as new
prices = get_recent_closes(calendar_days=100, columns="stock_id, date, close_price, volume")
ins = get_recent_ins(calendar_days=60)
a = old.rule1_watchlist(prices, ins=ins, min_volume=500, **GRANVILLE_DEFAULTS)
b = new.rule1_watchlist(prices, ins=ins, min_volume=500, **GRANVILLE_DEFAULTS)
pd.testing.assert_frame_equal(a, b); print("rule1_watchlist SAME", len(a))
for name, fn in [("granville", granville_followup), ("momentum", momentum_followup)]:
    t = time.time(); d, df = fn(); print(name, d, len(df), f"{time.time()-t:.1f}s")
    if not df.empty:
        print(followup_summary(df))
        cols = [c for c in ["signal_date", "stock_id", "stock_name", "signal", "setup_date", "buy_date", "buy_price",
                            "last_close", "held_days", "ret_now", "ret_max", "ret_min", "index_ret", "excess"] if c in df]
        print(df[cols].head(40).to_string())
