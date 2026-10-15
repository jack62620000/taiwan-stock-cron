-- 每日選股訊號：signals.py 每個交易日幫全市場股票打分數後寫進這張表
-- 總分 100 = 籌碼 25 + 資金 25 + 基本面 25 + 技術面 25
create table if not exists public.daily_signals (
    stock_id      varchar  not null references public.stock_info(stock_id),
    date          date     not null,
    total_score   smallint not null,
    chip_score    smallint not null,
    margin_score  smallint not null,
    fund_score    smallint not null,
    tech_score    smallint not null,
    rank          integer,
    reasons       text[]   not null default '{}',
    warnings      text[]   not null default '{}',
    close_price   numeric,
    change_5d     numeric,
    change_20d    numeric,
    avg_volume_20 bigint,
    liquid        boolean  not null default false,
    primary key (stock_id, date)
);

-- 網頁查「某一天的排行榜」用
create index if not exists daily_signals_date_score_idx
    on public.daily_signals (date, total_score desc);

comment on table  public.daily_signals is '每日選股訊號評分（signals.py 產生）';
comment on column public.daily_signals.stock_id      is '股票代號';
comment on column public.daily_signals.date          is '交易日期';
comment on column public.daily_signals.total_score   is '總分（0~100）';
comment on column public.daily_signals.chip_score    is '籌碼面分數（0~25）：外資、投信、大戶';
comment on column public.daily_signals.margin_score  is '資金面分數（0~25）：融資使用率、資減價漲、券資比';
comment on column public.daily_signals.fund_score    is '基本面分數（0~25）：月營收成長';
comment on column public.daily_signals.tech_score    is '技術面分數（0~25）：均線、量能、RSI';
comment on column public.daily_signals.rank          is '當日排名（只排有流動性的股票，其餘為空）';
comment on column public.daily_signals.reasons       is '加分理由';
comment on column public.daily_signals.warnings      is '風險提醒（不扣分，僅提示）';
comment on column public.daily_signals.close_price   is '收盤價';
comment on column public.daily_signals.change_5d     is '近5日漲跌幅（%）';
comment on column public.daily_signals.change_20d    is '近20日漲跌幅（%）';
comment on column public.daily_signals.avg_volume_20 is '20日均量（張）';
comment on column public.daily_signals.liquid        is '是否有足夠流動性（20日均量 >= 500 張）';

-- 規則回測：每個評分日的前 N 名，持有 h 個交易日後的平均報酬，對照同日所有有流動性股票的平均
-- 網頁「規則回測」區塊用 supabase.rpc("signal_performance", {...}) 呼叫
create or replace function public.signal_performance(top_n int default 20, horizon int default 5)
returns table (date date, fwd_date date, top_return numeric, market_return numeric, top_count int)
language sql stable
as $$
  with d as (
    select distinct s.date from public.daily_signals s
  ), dd as (
    select d.date, lead(d.date, horizon) over (order by d.date) as fwd_date from d
  )
  select s.date,
         dd.fwd_date,
         round(avg(f.close_price / s.close_price - 1) filter (where s.rank <= top_n) * 100, 2) as top_return,
         round(avg(f.close_price / s.close_price - 1) * 100, 2) as market_return,
         (count(*) filter (where s.rank <= top_n))::int as top_count
  from public.daily_signals s
  join dd on dd.date = s.date and dd.fwd_date is not null
  join public.daily_signals f on f.stock_id = s.stock_id and f.date = dd.fwd_date
  where s.liquid and s.close_price > 0
  group by s.date, dd.fwd_date
  order by s.date;
$$;

-- 2026-10-14 規則 v2：基本面改為 0~35、技術面改為 0~15，並記錄規則版本
alter table public.daily_signals add column if not exists rule_version smallint not null default 1;
comment on column public.daily_signals.rule_version is '評分規則版本（signals.py 的 RULE_VERSION）';
comment on column public.daily_signals.fund_score is '基本面分數（v2: 0~35）：月營收成長';
comment on column public.daily_signals.tech_score is '技術面分數（v2: 0~15）：月線、RSI、20日漲幅';

-- 2026-10-15 規則回測加速：signal_performance 只需要這幾欄，覆蓋索引讓它不必讀整張表（含 reasons/warnings 陣列）
-- 沒有這個索引時，資料庫快取冷的情況下要 4~5 秒，會超過 API 的 3 秒逾時
create index if not exists daily_signals_date_stock_cover_idx
    on public.daily_signals (date, stock_id) include (close_price, rank, liquid);

-- 2026-10-15 評分表瘦身：最近 keep_days 個評分日保留完整資料；更早的評分日
--   1. 刪掉當天沒列入排名、而且前 lookback 個評分日也都沒列入排名的股票（規則回測用不到）
--   2. 清空加分理由（reasons），風險提醒（warnings）很小，保留
-- signals.py 每次評分完會呼叫，只處理剛超過保留期的 max_dates 個評分日；回傳刪除筆數
create or replace function public.prune_daily_signals(keep_days int default 60, lookback int default 20, max_dates int default 5)
returns int
language plpgsql
as $$
declare
  cutoff date;
  d      date;
  since  date;
  k      int;
  n      int := 0;
begin
  select x.date into cutoff
  from (select distinct s.date from public.daily_signals s order by s.date desc offset keep_days - 1 limit 1) x;
  if cutoff is null then
    return 0;
  end if;

  for d in
    select distinct s.date from public.daily_signals s
    where s.date < cutoff order by s.date desc limit max_dates
  loop
    select min(x.date) into since
    from (select distinct s.date from public.daily_signals s
          where s.date < d order by s.date desc limit lookback) x;

    delete from public.daily_signals s
    where s.date = d and not s.liquid
      and not exists (
        select 1 from public.daily_signals t
        where t.stock_id = s.stock_id and t.liquid and t.date >= coalesce(since, d) and t.date < d
      );
    get diagnostics k = row_count;
    n := n + k;

    update public.daily_signals set reasons = '{}' where date = d and reasons <> '{}';
  end loop;
  return n;
end;
$$;

-- 2026-10-15 規則回測改用還原權值報酬：持有期間（評分日之後、到期日當天以前）若有除權息、減資、變更面額，
-- 報酬再除以這段期間各事件 factor 的乘積，除息、分割不會被算成虧損。adj 只對 price_adjustments 小表運算，速度影響很小
create or replace function public.signal_performance(top_n int default 20, horizon int default 5)
returns table (date date, fwd_date date, top_return numeric, market_return numeric, top_count int)
language sql stable
as $$
  with d as (
    select distinct s.date from public.daily_signals s
  ), dd as (
    select d.date, lead(d.date, horizon) over (order by d.date) as fwd_date from d
  ), adj as (
    select a.stock_id, dd.date, exp(sum(ln(a.factor))) as factor
    from dd
    join public.price_adjustments a on a.date > dd.date and a.date <= dd.fwd_date
    group by a.stock_id, dd.date
  )
  select s.date,
         dd.fwd_date,
         round(avg(f.close_price / s.close_price / coalesce(adj.factor, 1) - 1) filter (where s.rank <= top_n) * 100, 2) as top_return,
         round(avg(f.close_price / s.close_price / coalesce(adj.factor, 1) - 1) * 100, 2) as market_return,
         (count(*) filter (where s.rank <= top_n))::int as top_count
  from public.daily_signals s
  join dd on dd.date = s.date and dd.fwd_date is not null
  join public.daily_signals f on f.stock_id = s.stock_id and f.date = dd.fwd_date
  left join adj on adj.stock_id = s.stock_id and adj.date = s.date
  where s.liquid and s.close_price > 0
  group by s.date, dd.fwd_date
  order by s.date;
$$;
