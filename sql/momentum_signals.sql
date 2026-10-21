-- 動能選股：momentum.py 每個交易日收盤後檢查趨勢模板（Minervini）與 52 週新高突破，寫進這張表
-- 只保留最近 5 個交易日（momentum.py 會刪除更早的資料），全市場一天約 1,900 筆、約 2 MB；
-- 有訊號的列（新高突破、剛符合趨勢模板）多留到 30 個交易日，給每日選股頁的事後追蹤（每天約十幾～幾十筆）
create table if not exists public.momentum_signals (
    stock_id       varchar  not null references public.stock_info(stock_id),
    date           date     not null,
    close_price    numeric,
    ma50           numeric,
    ma150          numeric,
    ma200          numeric,
    ma200_prev     numeric,
    high_52w       numeric,
    low_52w        numeric,
    rs_rating      smallint,
    vol_ratio      numeric,
    atr14          numeric,
    trend_checks   smallint not null default 0,
    trend_template boolean  not null default false,
    trend_new      boolean  not null default false,
    new_high       boolean  not null default false,
    breakout       boolean  not null default false,
    liquid         boolean  not null default false,
    primary key (stock_id, date)
);

-- 和其他表一樣開 RLS、不設規則：只有 service_role 金鑰（網站、排程）讀寫得到，anon 金鑰讀不到
alter table public.momentum_signals enable row level security;

-- 網頁查「最新一天的全市場資料」用
create index if not exists momentum_signals_date_idx on public.momentum_signals (date);

comment on table  public.momentum_signals is '動能選股：趨勢模板與 52 週新高突破（momentum.py 產生）';
comment on column public.momentum_signals.stock_id       is '股票代號';
comment on column public.momentum_signals.date           is '交易日期';
comment on column public.momentum_signals.close_price    is '收盤價（原始價格）';
comment on column public.momentum_signals.ma50           is '50 日均線（還原權值）';
comment on column public.momentum_signals.ma150          is '150 日均線（還原權值）';
comment on column public.momentum_signals.ma200          is '200 日均線（還原權值）';
comment on column public.momentum_signals.ma200_prev     is '20 個交易日前的 200 日均線';
comment on column public.momentum_signals.high_52w       is '52 週（252 個交易日）最高收盤價';
comment on column public.momentum_signals.low_52w        is '52 週（252 個交易日）最低收盤價';
comment on column public.momentum_signals.rs_rating      is '相對強度 0~99：近 3/6/9/12 個月漲幅加權後，贏過多少 % 的有流動性股票';
comment on column public.momentum_signals.vol_ratio      is '當天成交量 ÷ 前 50 日均量';
comment on column public.momentum_signals.atr14          is '14 日平均真實波幅（元，給 ATR 停損用）';
comment on column public.momentum_signals.trend_checks   is '趨勢模板 8 個條件符合幾個';
comment on column public.momentum_signals.trend_template is '8 個條件全部符合（且 20 日均量 >= 500 張）';
comment on column public.momentum_signals.trend_new      is '今天剛符合趨勢模板（前一個交易日還不符合）';
comment on column public.momentum_signals.new_high       is '收盤創 52 週新高';
comment on column public.momentum_signals.breakout       is '創 52 週新高且量比 >= 1.5，前 10 個交易日內沒突破過（且 20 日均量 >= 500 張）';
comment on column public.momentum_signals.liquid         is '20 日均量 >= 500 張';
