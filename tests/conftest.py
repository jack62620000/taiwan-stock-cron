"""
網站的自動測試：用假的 Supabase（tests/fake_supabase.py）跑每一頁，確認不會出錯。
執行方式：在專案根目錄 pip install -r requirements.txt pytest，然後 pytest
"""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
sys.path.insert(0, str(APP))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_supabase import FakeClient  # noqa: E402
import fake_data  # noqa: E402

# utils.db 一載入就會連 Supabase，所以在載入前換成假的
_fake_module = types.ModuleType("supabase")
_fake_module.create_client = lambda url, key, options=None: FakeClient()
_fake_module.ClientOptions = lambda **kwargs: None
sys.modules["supabase"] = _fake_module


def make_client(empty=False):
    """empty=True：只有股票清單，其他表都還沒有資料（例如剛建好的新表、排程還沒跑）"""
    tables = fake_data.build_tables()
    if empty:
        tables = {"stock_info": tables["stock_info"]}
    return FakeClient(tables, rpcs={
        "signal_performance": fake_data.signal_performance,
        "recent_prices": fake_data.recent_prices,
        "recent_ins": fake_data.recent_ins,
    })


@pytest.fixture
def use_db():
    """換上一份新的假資料庫，並清掉網站的快取；回傳設定函式 use_db(empty=False)"""
    import streamlit as st
    import utils.db as db

    def _set(empty=False):
        client = make_client(empty)
        db.supabase = client
        db.etf_supabase = client
        st.cache_data.clear()
        db.get_stock_list.cache_clear()
        return client
    return _set
