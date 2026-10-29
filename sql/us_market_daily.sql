-- 美股收盤資料（ETF 專用資料庫「台股ETF資料庫」）：每日簡報用
-- 在那個專案的 SQL Editor 整份貼上執行一次即可；重複執行不會出錯也不會刪資料。
-- fetch_us_market.py（us_market.yml 排程）用 GitHub secrets 的 ETF_SUPABASE_URL / ETF_SUPABASE_KEY（service_role 金鑰）寫入。
create table if not exists public.us_market_daily (
    symbol      varchar(20)  not null,
    date        date         not null,
    name        text         not null,
    category    varchar(10)  not null,
    open        numeric,
    high        numeric,
    low         numeric,
    close       numeric      not null,
    prev_close  numeric,
    change      numeric,
    change_pct  numeric,
    volume      bigint,
    source      varchar(10)  not null,
    updated_at  timestamptz  not null default now(),
    primary key (symbol, date)
);
alter table public.us_market_daily enable row level security;

comment on table  public.us_market_daily is '美股收盤：指數、七巨頭、半導體、台灣 ADR、原油、匯率、美債殖利率（fetch_us_market.py 產生）';
comment on column public.us_market_daily.symbol     is 'Yahoo 代號（^SOX、TSM、CL=F…）；美債殖利率是 UST2Y、UST10Y、UST30Y';
comment on column public.us_market_daily.date       is '美國交易日（交易所當地日期，不是台灣日期）';
comment on column public.us_market_daily.category   is 'index＝指數與 VIX、mag7＝科技七巨頭、semi＝AI／半導體、adr＝台灣 ADR、commodity＝原油、fx＝匯率、bond＝美債殖利率';
comment on column public.us_market_daily.close      is '收盤價；美元計價（台幣匯率是每 1 美元兌台幣）；美債殖利率單位是 %';
comment on column public.us_market_daily.prev_close is '前一個交易日收盤（抓取區間第一天沒有）';
comment on column public.us_market_daily.change     is '漲跌（美債是百分點，×100 為 bp）';
comment on column public.us_market_daily.change_pct is '漲跌幅 %（美債殖利率的漲跌幅意義不大，簡報看 change）';
comment on column public.us_market_daily.source     is 'yahoo 或 treasury（美國財政部）';
