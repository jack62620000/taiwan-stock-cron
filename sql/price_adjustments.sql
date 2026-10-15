-- 還原權值事件：除權息、減資、變更面額（分割）
-- fetch_adjustments.py 抓取後寫入；網頁計算技術指標時，把事件日之前的股價乘上 factor，前後股價就能連續
create table if not exists public.price_adjustments (
    stock_id    varchar(10)  not null,
    date        date         not null,
    event_type  varchar(10)  not null,
    prev_close  numeric      not null,
    ref_price   numeric      not null,
    factor      numeric      not null,
    source      varchar(10)  not null,
    primary key (stock_id, date, event_type)
);

comment on table  public.price_adjustments is '還原權值事件（fetch_adjustments.py 產生）';
comment on column public.price_adjustments.stock_id   is '股票代號';
comment on column public.price_adjustments.date       is '事件生效日（除權息交易日／恢復買賣日）';
comment on column public.price_adjustments.event_type is '除權息、減資、變更面額';
comment on column public.price_adjustments.prev_close is '事件前最後一個交易日的收盤價';
comment on column public.price_adjustments.ref_price  is '交易所公布的參考價';
comment on column public.price_adjustments.factor     is '調整比例 = 參考價 ÷ 事件前收盤價；事件日之前的股價乘上這個比例';
comment on column public.price_adjustments.source     is '資料來源：證交所、櫃買中心、FinMind';
