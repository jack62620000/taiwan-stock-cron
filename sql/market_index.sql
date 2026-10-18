-- 大盤指數：加權指數（TAIEX）與櫃買指數（TPEX）每日行情
-- fetch_market_index.py 抓取後寫入；每日排程重抓當月，區間回補用 DATASETS=index
create table if not exists public.market_index (
    index_code   varchar(10)  not null,
    date         date         not null,
    open         numeric,
    high         numeric,
    low          numeric,
    close        numeric      not null,
    change       numeric,
    trade_value  bigint,
    primary key (index_code, date)
);

comment on table  public.market_index is '大盤指數每日行情（fetch_market_index.py 產生）';
comment on column public.market_index.index_code  is 'TAIEX＝發行量加權股價指數（上市）、TPEX＝櫃買指數（上櫃）';
comment on column public.market_index.date        is '交易日';
comment on column public.market_index.open        is '開盤指數';
comment on column public.market_index.high        is '最高指數';
comment on column public.market_index.low         is '最低指數';
comment on column public.market_index.close       is '收盤指數';
comment on column public.market_index.change      is '漲跌點數';
comment on column public.market_index.trade_value is '整體市場成交金額（元）';
