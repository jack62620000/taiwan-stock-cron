-- 所有資料表開啟 Row Level Security（2026-10-19 已執行）
-- 不設任何規則（policy）：anon / authenticated 金鑰讀寫都會被擋；
-- 網站（Streamlit secrets）與排程（GitHub secrets）的 SUPABASE_KEY 都要用 service_role 金鑰，它會略過 RLS。
-- 之後新建的表也要記得開。
alter table public.stock_info enable row level security;
alter table public.daily_prices enable row level security;
alter table public.stock_ins_daily enable row level security;
alter table public.holder_distribution enable row level security;
alter table public.monthly_revenue enable row level security;
alter table public.quarterly_report enable row level security;
alter table public.futures_institutional enable row level security;
alter table public.margin_utilization enable row level security;
alter table public.daily_signals enable row level security;
alter table public.price_adjustments enable row level security;
alter table public.market_index enable row level security;
alter table public.watchlist enable row level security;
alter table public.paper_trades enable row level security;
alter table public.watch_groups enable row level security;
