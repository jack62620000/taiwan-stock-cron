"""
測試用的假 Supabase：資料放在記憶體裡，支援網站用到的查詢語法
（select／eq／in_／gt／gte／lt／lte／order／limit／range／insert／upsert／update／delete／rpc）。
"""
import itertools
from datetime import datetime, timezone

# 各表的主鍵，upsert 時用來判斷是新增還是覆蓋
PRIMARY_KEYS = {
    "watchlist": ("group_id", "stock_id"),
    "watch_groups": ("id",),
    "paper_trades": ("id",),
}

# 資料庫自動填的欄位（timestamptz 預設 now()）
DEFAULT_NOW = {"watchlist": "added_at", "watch_groups": "created_at", "paper_trades": "created_at"}


class Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class Query:
    def __init__(self, client, table):
        self.client = client
        self.table = table
        self.filters = []
        self.orders = []
        self.columns = "*"
        self.count = None
        self.start, self.stop = 0, None
        self.action = "select"
        self.payload = None

    # ----- 讀取 -----
    def select(self, columns="*", count=None, **_):
        self.columns, self.count = columns, count
        return self

    def _filter(self, col, fn):
        self.filters.append((col, fn))
        return self

    def eq(self, col, v):
        return self._filter(col, lambda x: x is not None and str(x) == str(v))

    def in_(self, col, values):
        values = {str(v) for v in values}
        return self._filter(col, lambda x: x is not None and str(x) in values)

    def gt(self, col, v):
        return self._filter(col, lambda x: x is not None and _cmp(x, v) > 0)

    def gte(self, col, v):
        return self._filter(col, lambda x: x is not None and _cmp(x, v) >= 0)

    def lt(self, col, v):
        return self._filter(col, lambda x: x is not None and _cmp(x, v) < 0)

    def lte(self, col, v):
        return self._filter(col, lambda x: x is not None and _cmp(x, v) <= 0)

    def order(self, col, desc=False):
        self.orders.append((col, desc))
        return self

    def limit(self, n):
        self.stop = self.start + n
        return self

    def range(self, start, end):
        self.start, self.stop = start, end + 1
        return self

    # ----- 寫入 -----
    def insert(self, rows):
        self.action, self.payload = "insert", rows
        return self

    def upsert(self, rows, **_):
        self.action, self.payload = "upsert", rows
        return self

    def update(self, values):
        self.action, self.payload = "update", values
        return self

    def delete(self, **_):
        self.action = "delete"
        return self

    def _match(self, row):
        return all(fn(row.get(col)) for col, fn in self.filters)

    def execute(self):
        rows = self.client.tables.setdefault(self.table, [])
        if self.action in ("insert", "upsert"):
            new = self.payload if isinstance(self.payload, list) else [self.payload]
            keys = PRIMARY_KEYS.get(self.table)
            for r in new:
                r = dict(r)
                if self.table in DEFAULT_NOW:
                    r.setdefault(DEFAULT_NOW[self.table], datetime.now(timezone.utc).isoformat())
                if keys == ("id",) and "id" not in r:
                    r["id"] = next(self.client.ids)
                if self.action == "upsert" and keys:
                    rows[:] = [x for x in rows if any(x.get(k) != r.get(k) for k in keys)]
                rows.append(r)
            return Result(new)
        if self.action == "update":
            hit = [r for r in rows if self._match(r)]
            for r in hit:
                r.update(self.payload)
            return Result(hit)
        if self.action == "delete":
            hit = [r for r in rows if self._match(r)]
            rows[:] = [r for r in rows if not self._match(r)]
            return Result(hit, len(hit))

        out = [r for r in rows if self._match(r)]
        for col, desc in reversed(self.orders):
            out.sort(key=lambda r: (r.get(col) is None, r.get(col) if r.get(col) is not None else 0), reverse=desc)
        total = len(out)
        out = out[self.start:self.stop]
        if self.columns.strip() != "*":
            cols = [c.strip() for c in self.columns.split(",")]
            out = [{c: r.get(c) for c in cols} for r in out]
        else:
            out = [dict(r) for r in out]
        return Result(out, total if self.count else None)


def _cmp(a, b):
    if isinstance(a, (int, float)) and not isinstance(b, str):
        return (a > b) - (a < b)
    a, b = str(a), str(b)
    return (a > b) - (a < b)


class RPC:
    def __init__(self, data):
        self.data = data

    def range(self, start, end):
        if isinstance(self.data, list):
            return RPC(self.data[start:end + 1])
        return self

    def execute(self):
        return Result(self.data)


class FakeBucket:
    """假的 Storage 桶：檔案存在 dict 裡"""
    def __init__(self, files):
        self.files = files

    def upload(self, path, data, file_options=None):
        upsert = str((file_options or {}).get("upsert", "")).lower() == "true"
        if path in self.files and not upsert:
            raise RuntimeError(f"{path} 已存在")
        self.files[path] = bytes(data)
        return {"path": path}

    def download(self, path, *_, **__):
        if path not in self.files:
            raise RuntimeError(f"{path} 不存在")
        return self.files[path]

    def list(self, path=None, options=None):
        prefix = (path or "").rstrip("/") + "/"
        names = sorted(p[len(prefix):] for p in self.files if p.startswith(prefix) and "/" not in p[len(prefix):])
        return [{"name": n} for n in names]


class FakeStorage:
    def __init__(self):
        self.buckets = {}

    def from_(self, bucket):
        return FakeBucket(self.buckets.setdefault(bucket, {}))


class FakeClient:
    def __init__(self, tables=None, rpcs=None):
        self.tables = tables if tables is not None else {}
        self.rpcs = rpcs or {}
        self.storage = FakeStorage()
        self.ids = itertools.count(100)  # 假資料裡的 id 從 1 開始，新增的從 100 開始

    def table(self, name):
        return Query(self, name)

    def rpc(self, name, params=None):
        fn = self.rpcs.get(name)
        return RPC(fn(self, **(params or {})) if fn else [])
