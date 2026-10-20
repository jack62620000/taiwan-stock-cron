-- 網站與排程用到的資料庫函式（2026-10 建立）

-- 資料庫目前用量（bytes），每日資料檢查（check_data.py）用來提醒是否接近免費額度 500 MB
create or replace function public.database_size()
returns bigint
language sql stable
set search_path = public
as $$
    select pg_database_size(current_database());
$$;

-- 全市場近期股價，每檔股票一列、日期與價格放在陣列裡（總覽、葛蘭碧頁用）。
-- 逐檔走 (stock_id, date) 主鍵索引，100 天全市場約 0.2 秒；
-- 回傳約 2 千列，比原本逐日查詢的十幾萬列少很多，網頁載入快很多。
create or replace function public.recent_prices(start_date date)
returns table (stock_id varchar, dates date[], closes numeric[], volumes bigint[])
language sql stable
set search_path = public
as $$
    select s.stock_id, p.dates, p.closes, p.volumes
    from stock_info s
    cross join lateral (
        select array_agg(dp.date order by dp.date) as dates,
               array_agg(dp.close_price order by dp.date) as closes,
               array_agg(dp.volume order by dp.date) as volumes
        from daily_prices dp
        where dp.stock_id = s.stock_id and dp.date >= start_date
    ) p
    where p.dates is not null
    order by s.stock_id;
$$;

-- 全市場近期外資、投信買賣超，格式同 recent_prices（總覽類股輪動、葛蘭碧籌碼條件用）
create or replace function public.recent_ins(start_date date)
returns table (stock_id varchar, dates date[], foreign_net bigint[], sitca_net bigint[])
language sql stable
set search_path = public
as $$
    select s.stock_id, p.dates, p.foreign_net, p.sitca_net
    from stock_info s
    cross join lateral (
        select array_agg(i.date order by i.date) as dates,
               array_agg(i.foreign_net_shares order by i.date) as foreign_net,
               array_agg(i.sitca_net_shares order by i.date) as sitca_net
        from stock_ins_daily i
        where i.stock_id = s.stock_id and i.date >= start_date
    ) p
    where p.dates is not null
    order by s.stock_id;
$$;

-- Supabase 安全檢查建議：函式固定 search_path
alter function public.signal_performance(integer, integer) set search_path = public;
alter function public.prune_daily_signals(integer, integer, integer) set search_path = public;
