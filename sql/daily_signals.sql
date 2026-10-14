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
