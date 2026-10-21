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

-- 每日選股訊號頁的回測統計：每個評分日的前 top_n 名 vs 全市場（20 日均量夠的股票）平均報酬（已還原權值）
-- 2026-10 改寫取評分日的方式：逐一跳到下一個日期（走日期索引），不必讀整張表
-- 2026-10 改成實際做得到的買法：分數收盤後才算得出來，所以「評分隔天收盤買進」，持有 horizon 個交易日後收盤賣出
--   （原本用評分當天收盤價買，實際買不到）。date 是評分日、fwd_date 是賣出日；交易成本由網頁扣除。
--   用隔天開盤價要查 daily_prices，全表 8 萬次查詢約 2.5 秒，容易超過 API 逾時，所以用 daily_signals 裡的收盤價（約 0.5 秒）
create or replace function public.signal_performance(top_n integer default 20, horizon integer default 5)
returns table(date date, fwd_date date, top_return numeric, market_return numeric, top_count integer)
language sql stable
set search_path = public
as $function$
  with recursive d(date) as (
    (select min(x.date) from public.daily_signals x)
    union all
    select (select min(x.date) from public.daily_signals x where x.date > d.date)
    from d where d.date is not null
  ), dd as (
    select d.date,
           lead(d.date, 1) over (order by d.date) as buy_date,
           lead(d.date, horizon + 1) over (order by d.date) as fwd_date
    from d where d.date is not null
  ), adj as (
    select a.stock_id, dd.date, exp(sum(ln(a.factor))) as factor
    from dd
    join public.price_adjustments a on a.date > dd.buy_date and a.date <= dd.fwd_date
    group by a.stock_id, dd.date
  )
  select s.date,
         dd.fwd_date,
         round(avg(f.close_price / b.close_price / coalesce(adj.factor, 1) - 1) filter (where s.rank <= top_n) * 100, 2) as top_return,
         round(avg(f.close_price / b.close_price / coalesce(adj.factor, 1) - 1) * 100, 2) as market_return,
         (count(*) filter (where s.rank <= top_n))::int as top_count
  from public.daily_signals s
  join dd on dd.date = s.date and dd.fwd_date is not null
  join public.daily_signals b on b.stock_id = s.stock_id and b.date = dd.buy_date and b.close_price > 0
  join public.daily_signals f on f.stock_id = s.stock_id and f.date = dd.fwd_date
  left join adj on adj.stock_id = s.stock_id and adj.date = s.date
  where s.liquid and s.close_price > 0
  group by s.date, dd.fwd_date
  order by s.date;
$function$;

-- 每日評分表每天更新，預設要變動 20% 才自動整理（VACUUM），期間索引查詢得回表確認、變慢。
-- 調成 2%，大約每天整理一次（2026-10 調整，當時也手動跑過一次 vacuum analyze）
alter table public.daily_signals set (
    autovacuum_vacuum_scale_factor = 0.02,
    autovacuum_vacuum_insert_scale_factor = 0.02,
    autovacuum_analyze_scale_factor = 0.02
);
