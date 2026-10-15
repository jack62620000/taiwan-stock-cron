-- 葛蘭碧紙上交易：paper_trade.py 每個交易日依固定條件模擬買賣後寫進這張表
-- 網頁「葛蘭碧八大法則 → 模擬交易」分頁讀取顯示
create table if not exists public.granville_paper_trades (
    id           bigint generated always as identity primary key,
    stock_id     varchar not null references public.stock_info(stock_id),
    signal_date  date    not null,
    entry_date   date,
    entry_price  numeric,
    exit_date    date,
    exit_price   numeric,
    exit_reason  text,
    status       text    not null default 'pending',
    return_pct   numeric,
    rules        int[]   not null default '{}',
    created_at   timestamptz not null default now(),
    unique (stock_id, signal_date)
);

comment on table  public.granville_paper_trades is '葛蘭碧紙上交易紀錄（paper_trade.py 產生）';
comment on column public.granville_paper_trades.signal_date is '出現買進訊號的交易日（收盤後）';
comment on column public.granville_paper_trades.entry_date  is '買進日（訊號隔天開盤）';
comment on column public.granville_paper_trades.status      is 'pending：明天開盤買／open：持有中／closed：已出場';
comment on column public.granville_paper_trades.return_pct  is '報酬（%，已扣手續費與證交稅）；open 時是用最新收盤價算的未實現報酬';
comment on column public.granville_paper_trades.rules       is '觸發買進的法則編號';
