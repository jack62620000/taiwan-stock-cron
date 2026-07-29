import os
import pandas as pd
from supabase import create_client
from dotenv import load_dotenv
from functools import lru_cache

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# ===== 基本資料 =====
@lru_cache(maxsize=1)
def get_stock_list():
    all_data = []
    start = 0
    while True:
        res = supabase.table("stock_info").select("*") \
            .order("stock_id", desc=False) \
            .range(start, start + 999).execute()
        if not res.data:
            break
        all_data.extend(res.data)
        if len(res.data) < 1000:
            break
        start += 1000
    return pd.DataFrame(all_data)

def get_stock_info(stock_id):
    res = supabase.table("stock_info").select("*").eq("stock_id", stock_id).execute()
    return res.data[0] if res.data else None

# ===== 股價 =====
def get_daily_prices(stock_id, limit=180):
    res = supabase.table("daily_prices") \
        .select("*") \
        .eq("stock_id", stock_id) \
        .order("date", desc=False) \
        .limit(limit) \
        .execute()
    return pd.DataFrame(res.data)

def get_latest_prices():
    res = supabase.table("daily_prices") \
        .select("date") \
        .order("date", desc=True) \
        .limit(1) \
        .execute()
    if not res.data:
        return pd.DataFrame()
    latest_date = res.data[0]["date"]
    all_data = []
    start = 0
    while True:
        r = supabase.table("daily_prices") \
            .select("stock_id, date, open_price, high_price, low_price, close_price, volume") \
            .gte("date", latest_date) \
            .order("stock_id", desc=False) \
            .range(start, start + 999) \
            .execute()
        if not r.data:
            break
        all_data.extend(r.data)
        if len(r.data) < 1000:
            break
        start += 1000
    if not all_data:
        return pd.DataFrame()
    df = pd.DataFrame(all_data)
    df = df.sort_values("date", ascending=False)
    df = df.drop_duplicates(subset="stock_id", keep="first")
    return df

# ===== 三大法人 =====
def get_stock_ins(stock_id, limit=60):
    res = supabase.table("stock_ins_daily") \
        .select("*") \
        .eq("stock_id", stock_id) \
        .order("date", desc=False) \
        .limit(limit) \
        .execute()
    return pd.DataFrame(res.data)

def get_latest_ins():
    res = supabase.table("stock_ins_daily") \
        .select("date") \
        .order("date", desc=True) \
        .limit(1) \
        .execute()
    if not res.data:
        return pd.DataFrame()
    latest_date = res.data[0]["date"]
    all_data = []
    start = 0
    while True:
        r = supabase.table("stock_ins_daily") \
            .select("*") \
            .eq("date", latest_date) \
            .order("stock_id", desc=False) \
            .range(start, start + 999) \
            .execute()
        if not r.data:
            break
        all_data.extend(r.data)
        if len(r.data) < 1000:
            break
        start += 1000
    return pd.DataFrame(all_data)

# ===== 融資融券 =====
def get_margin(stock_id, limit=60):
    res = supabase.table("margin_trading") \
        .select("*") \
        .eq("stock_id", stock_id) \
        .order("date", desc=False) \
        .limit(limit) \
        .execute()
    return pd.DataFrame(res.data)

def get_latest_margin():
    res = supabase.table("margin_trading") \
        .select("date") \
        .order("date", desc=True) \
        .limit(1) \
        .execute()
    if not res.data:
        return pd.DataFrame()
    latest_date = res.data[0]["date"]
    all_data = []
    start = 0
    while True:
        r = supabase.table("margin_trading") \
            .select("*") \
            .eq("date", latest_date) \
            .order("stock_id", desc=False) \
            .range(start, start + 999) \
            .execute()
        if not r.data:
            break
        all_data.extend(r.data)
        if len(r.data) < 1000:
            break
        start += 1000
    return pd.DataFrame(all_data)

# ===== 融資融券使用率 =====
def get_margin_utilization(stock_id, limit=60):
    """取得單一股票融資融券使用率歷史"""
    res = supabase.table("margin_utilization") \
        .select("*") \
        .eq("stock_id", stock_id) \
        .order("date", desc=False) \
        .limit(limit) \
        .execute()
    return pd.DataFrame(res.data)

def get_latest_margin_utilization():
    """取得所有股票最新融資融券使用率"""
    res = supabase.table("margin_utilization") \
        .select("date") \
        .order("date", desc=True) \
        .limit(1) \
        .execute()
    if not res.data:
        return pd.DataFrame()
    latest_date = res.data[0]["date"]
    all_data = []
    start = 0
    while True:
        r = supabase.table("margin_utilization") \
            .select("*") \
            .eq("date", latest_date) \
            .order("stock_id", desc=False) \
            .range(start, start + 999) \
            .execute()
        if not r.data:
            break
        all_data.extend(r.data)
        if len(r.data) < 1000:
            break
        start += 1000
    return pd.DataFrame(all_data)

# ===== 大戶持股 =====
def get_holder(stock_id, limit=20):
    res = supabase.table("holder_distribution") \
        .select("*") \
        .eq("stock_id", stock_id) \
        .order("week_date", desc=False) \
        .limit(limit) \
        .execute()
    return pd.DataFrame(res.data)

def get_latest_holder():
    res = supabase.table("holder_distribution") \
        .select("week_date") \
        .order("week_date", desc=True) \
        .limit(1) \
        .execute()
    if not res.data:
        return pd.DataFrame()
    latest_date = res.data[0]["week_date"]
    all_data = []
    start = 0
    while True:
        r = supabase.table("holder_distribution") \
            .select("*") \
            .eq("week_date", latest_date) \
            .order("stock_id", desc=False) \
            .range(start, start + 999) \
            .execute()
        if not r.data:
            break
        all_data.extend(r.data)
        if len(r.data) < 1000:
            break
        start += 1000
    return pd.DataFrame(all_data)

# ===== 月營收 =====
def get_revenue(stock_id, limit=12):
    res = supabase.table("monthly_revenue") \
        .select("*") \
        .eq("stock_id", stock_id) \
        .order("year_month", desc=False) \
        .limit(limit) \
        .execute()
    return pd.DataFrame(res.data)

def get_latest_revenue():
    res = supabase.table("monthly_revenue") \
        .select("year_month") \
        .order("year_month", desc=True) \
        .limit(1) \
        .execute()
    if not res.data:
        return pd.DataFrame()
    latest_ym = res.data[0]["year_month"]
    all_data = []
    start = 0
    while True:
        r = supabase.table("monthly_revenue") \
            .select("*") \
            .eq("year_month", latest_ym) \
            .order("stock_id", desc=False) \
            .range(start, start + 999) \
            .execute()
        if not r.data:
            break
        all_data.extend(r.data)
        if len(r.data) < 1000:
            break
        start += 1000
    return pd.DataFrame(all_data)

# ===== 季報 =====
def get_quarterly(stock_id, limit=8):
    res = supabase.table("quarterly_report") \
        .select("*") \
        .eq("stock_id", stock_id) \
        .order("year", desc=False) \
        .order("quarter", desc=False) \
        .limit(limit) \
        .execute()
    return pd.DataFrame(res.data)

def get_latest_quarterly():
    res = supabase.table("quarterly_report") \
        .select("updated_at") \
        .order("updated_at", desc=True) \
        .limit(1) \
        .execute()
    if not res.data:
        return pd.DataFrame()
    latest = res.data[0]["updated_at"]
    all_data = []
    start = 0
    while True:
        r = supabase.table("quarterly_report") \
            .select("*") \
            .eq("updated_at", latest) \
            .order("stock_id", desc=False) \
            .range(start, start + 999) \
            .execute()
        if not r.data:
            break
        all_data.extend(r.data)
        if len(r.data) < 1000:
            break
        start += 1000
    return pd.DataFrame(all_data)

# ===== 期貨三大法人 =====
def get_futures_ins(limit=60):
    res = supabase.table("futures_institutional") \
        .select("*") \
        .eq("contract_code", "臺股期貨") \
        .order("date", desc=False) \
        .limit(limit * 3) \
        .execute()
    return pd.DataFrame(res.data)