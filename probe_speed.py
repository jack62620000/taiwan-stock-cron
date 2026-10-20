"""暫時：在 GitHub Actions 上用真實資料庫量每一頁的載入時間（唯讀，測完就刪）"""
import sys, time, collections
from pathlib import Path
sys.path.insert(0, str(Path("app").resolve()))
from streamlit.testing.v1 import AppTest
import streamlit as st
import utils.db as db

STATS = collections.defaultdict(lambda: [0, 0.0])
CURRENT = ["?"]
BLOCK = {"insert", "upsert", "update", "delete"}


class RO:
    def __init__(self, obj, label):
        self._o, self._l = obj, label

    def __getattr__(self, name):
        if name in BLOCK:
            raise RuntimeError(f"唯讀測試：擋下 {name}")
        attr = getattr(self._o, name)
        if name == "execute":
            def run(*a, **k):
                t = time.perf_counter()
                try:
                    return attr(*a, **k)
                finally:
                    s = STATS[(CURRENT[0], self._l)]
                    s[0] += 1
                    s[1] += time.perf_counter() - t
            return run
        if callable(attr):
            def wrap(*a, **k):
                r = attr(*a, **k)
                label = self._l if name not in ("table", "rpc") else (a[0] if a else name)
                return RO(r, label) if hasattr(r, "execute") or hasattr(r, "select") else r
            return wrap
        return attr


db.supabase = RO(db.supabase, "client")
pages = ["main.py"] + sorted(p.name for p in Path("app/pages").glob("*.py"))
results = []
for p in pages:
    path = Path("app") / ("" if p == "main.py" else "pages") / p
    st.cache_data.clear()
    db.get_stock_list.cache_clear()
    CURRENT[0] = p
    at = AppTest.from_file(str(path), default_timeout=300)
    at.secrets["USERNAME"] = "x"
    at.secrets["PASSWORD"] = "y"
    at.session_state["authenticated"] = True
    at.session_state["user"] = "jack6262000"
    at.session_state["user_name"] = "測試"
    at.session_state["primary"] = True
    t = time.perf_counter()
    at.run()
    total = time.perf_counter() - t
    errs = [str(e.value)[:120] for e in at.exception]
    results.append((p, total, errs))
    print(f"PAGE {p}: {total:.1f}s errors={errs}", flush=True)

print("\n===== 每頁總時間 =====")
for p, t, e in sorted(results, key=lambda x: -x[1]):
    print(f"{t:6.1f}s  {p}  {'(有錯誤)' if e else ''}")
print("\n===== 每頁各資料表查詢（次數、秒） =====")
for (p, table), (n, secs) in sorted(STATS.items(), key=lambda x: -x[1][1])[:40]:
    print(f"{secs:6.1f}s {n:5d}次  {p}  {table}")
# 第二次：比較改善後
# 第三次：HTTP/1.1 連線池
