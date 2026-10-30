-- ETF 專用資料庫（另一個 Supabase 專案「台股ETF資料庫」）的 7 張表
-- 在新專案的 SQL Editor 整份貼上執行一次即可；重複執行不會出錯也不會刪資料。
-- 排程 etf_update.py 用 GitHub secrets 的 ETF_SUPABASE_URL / ETF_SUPABASE_KEY（service_role 金鑰）寫入。
-- 代號一律用文字，包含 00679B、00400A、00632R 這類英文字尾。

-- 1. ETF 清單與基本資料（每日更新）
create table if not exists public.etf_info (
    etf_id       varchar(10)  primary key,
    etf_name     text         not null,
    market       varchar(4)   not null,
    category     varchar(10),
    fund_type    text,
    index_name   text,
    listing_date date,
    fund_tax_id  varchar(10),
    updated_at   date         not null
);
comment on table  public.etf_info is 'ETF 清單：上市來自證交所 t187ap47_L，上櫃來自櫃買每日行情；基金統編由投信公會月資料補上';
comment on column public.etf_info.etf_id is 'ETF 代號，例如 0050、00679B';
comment on column public.etf_info.etf_name is 'ETF 簡稱';
comment on column public.etf_info.index_name is '追蹤指數名稱';
comment on column public.etf_info.listing_date is '上市（櫃）日期';
comment on column public.etf_info.market       is '上市／上櫃';
comment on column public.etf_info.category     is '國內股票、國外股票、債券、主動式、槓桿反向、期貨商品';
comment on column public.etf_info.fund_type    is '證交所原始基金類型（只有上市 ETF 有）';
comment on column public.etf_info.fund_tax_id  is '基金統編，用來對應投信公會 2024 年以前沒有證券代號的月資料';
comment on column public.etf_info.updated_at   is '最後一次在來源清單看到這檔的日期（太久沒更新代表已下市）';

-- 2. 每日價量
create table if not exists public.etf_daily_prices (
    etf_id       varchar(10) not null,
    date         date        not null,
    open_price   numeric,
    high_price   numeric,
    low_price    numeric,
    close_price  numeric,
    change       numeric,
    volume       bigint,
    trade_value  bigint,
    transactions integer,
    primary key (etf_id, date)
);
comment on table  public.etf_daily_prices is 'ETF 每日行情：上市 MI_INDEX（type=0099P），上櫃 afterTrading/otc';
comment on column public.etf_daily_prices.etf_id is 'ETF 代號';
comment on column public.etf_daily_prices.date is '交易日';
comment on column public.etf_daily_prices.open_price is '開盤價';
comment on column public.etf_daily_prices.high_price is '最高價';
comment on column public.etf_daily_prices.low_price is '最低價';
comment on column public.etf_daily_prices.close_price is '收盤價';
comment on column public.etf_daily_prices.change       is '漲跌（元）';
comment on column public.etf_daily_prices.volume       is '成交股數（單位數）';
comment on column public.etf_daily_prices.trade_value  is '成交金額（元）';
comment on column public.etf_daily_prices.transactions is '成交筆數';

-- 3. 淨值、折溢價、發行單位數
create table if not exists public.etf_nav (
    etf_id       varchar(10) not null,
    date         date        not null,
    market_price numeric,
    est_nav      numeric,
    premium_pct  numeric,
    nav          numeric,
    units        bigint,
    units_change bigint,
    primary key (etf_id, date)
);
comment on table  public.etf_nav is 'ETF 每日淨值：證交所 MIS all_etf.txt 收盤後快照；沒有歷史，從開始抓的那天累積';
comment on column public.etf_nav.etf_id is 'ETF 代號';
comment on column public.etf_nav.date is '交易日';
comment on column public.etf_nav.market_price is '市價（收盤）';
comment on column public.etf_nav.est_nav      is '投信盤後揭露的預估淨值';
comment on column public.etf_nav.premium_pct  is '折溢價 %（市價相對預估淨值，正數為溢價）';
comment on column public.etf_nav.nav          is '正式淨值：隔一個交易日由 MIS 的「前一日淨值」回寫';
comment on column public.etf_nav.units        is '已發行受益權單位數';
comment on column public.etf_nav.units_change is '單位數較前一日增減（正數為申購淨流入）';

-- 4. 配息
create table if not exists public.etf_dividends (
    etf_id        varchar(10) not null,
    ex_date       date        not null,
    record_date   date,
    pay_date      date,
    cash_dividend numeric,
    primary key (etf_id, ex_date)
);
comment on table  public.etf_dividends is 'ETF 收益分配：證交所 etfDiv、TWT48U_ALL，櫃買 tpex_exright_prepost';
comment on column public.etf_dividends.etf_id is 'ETF 代號';
comment on column public.etf_dividends.ex_date       is '除息交易日';
comment on column public.etf_dividends.record_date   is '收益分配基準日';
comment on column public.etf_dividends.pay_date      is '收益分配發放日';
comment on column public.etf_dividends.cash_dividend is '每單位配息（元）；尚未公告金額時為空';

-- 5. 三大法人買賣超
create table if not exists public.etf_ins_daily (
    etf_id             varchar(10) not null,
    date               date        not null,
    foreign_net_shares bigint,
    sitca_net_shares   bigint,
    dealers_net_shares bigint,
    primary key (etf_id, date)
);
comment on table  public.etf_ins_daily is 'ETF 三大法人買賣超（股數）：上市 T86（selectType=0099P），上櫃 insti/dailyTrade；定義與 stock_ins_daily 相同';
comment on column public.etf_ins_daily.etf_id is 'ETF 代號';
comment on column public.etf_ins_daily.date is '交易日';
comment on column public.etf_ins_daily.foreign_net_shares is '外資（含外資自營商）';
comment on column public.etf_ins_daily.sitca_net_shares   is '投信';
comment on column public.etf_ins_daily.dealers_net_shares is '自營商（自行買賣＋避險）';

-- 6. 每月規模、受益人數、申購買回、定期定額
create table if not exists public.etf_monthly_stats (
    etf_id              varchar(10) not null,
    year_month          varchar(7)  not null,
    aum                 bigint,
    nav                 numeric,
    units               bigint,
    holders_natural     integer,
    holders_legal       integer,
    holders_total       integer,
    subscription_amount bigint,
    redemption_amount   bigint,
    dca_count           integer,
    dca_accounts        integer,
    dca_amount          bigint,
    primary key (etf_id, year_month)
);
comment on table  public.etf_monthly_stats is 'ETF 每月統計：投信投顧公會 etf_statement2（金額一律台幣）；期貨信託類 ETF 不在公會資料內';
comment on column public.etf_monthly_stats.etf_id is 'ETF 代號';
comment on column public.etf_monthly_stats.year_month          is '資料月份，例如 2026-09';
comment on column public.etf_monthly_stats.aum                 is '基金規模（元）';
comment on column public.etf_monthly_stats.nav                 is '月底單位淨值';
comment on column public.etf_monthly_stats.units               is '受益權單位數';
comment on column public.etf_monthly_stats.holders_natural is '受益人數：自然人';
comment on column public.etf_monthly_stats.holders_legal is '受益人數：法人';
comment on column public.etf_monthly_stats.holders_total is '受益人數：合計';
comment on column public.etf_monthly_stats.subscription_amount is '本月申購總金額（元）';
comment on column public.etf_monthly_stats.redemption_amount   is '本月買回總金額（元）';
comment on column public.etf_monthly_stats.dca_count           is '本月定期定額扣款筆數';
comment on column public.etf_monthly_stats.dca_accounts        is '本月定期定額扣款戶數';
comment on column public.etf_monthly_stats.dca_amount          is '本月定期定額扣款金額（元）';

-- 7. 每週集保持股分級摘要
create table if not exists public.etf_holder_distribution (
    etf_id             varchar(10) not null,
    week_date          date        not null,
    total_holders      integer,
    big_holder_ratio   numeric,
    small_holder_ratio numeric,
    primary key (etf_id, week_date)
);
comment on table  public.etf_holder_distribution is 'ETF 集保持股分級摘要：集保開放資料 1-5（最新一週），歷史由集保查詢網頁回補約一年';
comment on column public.etf_holder_distribution.etf_id is 'ETF 代號';
comment on column public.etf_holder_distribution.week_date is '集保資料日期（每週）';
comment on column public.etf_holder_distribution.total_holders      is '受益人數（持股分級 1～15 人數合計）';
comment on column public.etf_holder_distribution.big_holder_ratio   is '400 張以上大戶持有比例 %（分級 12～15）';
comment on column public.etf_holder_distribution.small_holder_ratio is '20 張以下散戶持有比例 %（分級 1～5）';

-- 和主資料庫一樣開啟 RLS、不設規則：只有 service_role 金鑰能讀寫
alter table public.etf_info                enable row level security;
alter table public.etf_daily_prices        enable row level security;
alter table public.etf_nav                 enable row level security;
alter table public.etf_dividends           enable row level security;
alter table public.etf_ins_daily           enable row level security;
alter table public.etf_monthly_stats       enable row level security;
alter table public.etf_holder_distribution enable row level security;

-- 8. 融資融券（2026-10 新增）
create table if not exists public.etf_margin_daily (
    etf_id         varchar(10) not null,
    date           date        not null,
    margin_balance bigint,
    margin_limit   bigint,
    margin_rate    numeric,
    short_balance  bigint,
    short_rate     numeric,
    primary key (etf_id, date)
);
comment on table  public.etf_margin_daily is 'ETF 融資融券（張）：上市 MI_MARGN（selectType=0099P），上櫃 margin/balance；定義與 margin_utilization 相同，保留 1 年';
comment on column public.etf_margin_daily.etf_id         is 'ETF 代號';
comment on column public.etf_margin_daily.date           is '交易日';
comment on column public.etf_margin_daily.margin_balance is '融資餘額（張）';
comment on column public.etf_margin_daily.margin_limit   is '融資限額（張）';
comment on column public.etf_margin_daily.margin_rate    is '融資使用率 %（餘額／限額）';
comment on column public.etf_margin_daily.short_balance  is '融券餘額（張）';
comment on column public.etf_margin_daily.short_rate     is '融券使用率 %（餘額／限額）';

-- 9. 每月前十大持股（2026-10 新增）
create table if not exists public.etf_top_holdings (
    etf_id       varchar(10) not null,
    year_month   varchar(7)  not null,
    rank         smallint    not null,
    holding_type text,
    holding_code text,
    holding_name text,
    amount       bigint,
    weight_pct   numeric,
    primary key (etf_id, year_month, rank)
);
comment on table  public.etf_top_holdings is 'ETF 每月前十大持股：投信投顧公會「基金投資明細－月前十大」（2021-09 起），用基金名稱對回代號；保留 3 年';
comment on column public.etf_top_holdings.etf_id       is 'ETF 代號';
comment on column public.etf_top_holdings.year_month   is '資料月份，例如 2026-09';
comment on column public.etf_top_holdings.rank         is '名次（1～10）';
comment on column public.etf_top_holdings.holding_type is '標的種類，例如國內上市、國外股票、政府公債';
comment on column public.etf_top_holdings.holding_code is '標的代號（台股代號或國際證券代碼 ISIN）';
comment on column public.etf_top_holdings.holding_name is '標的名稱';
comment on column public.etf_top_holdings.amount       is '持有金額（元）';
comment on column public.etf_top_holdings.weight_pct   is '占基金淨資產價值比例 %';

-- 10. 借券賣出餘額（2026-10 新增）
create table if not exists public.etf_sbl_daily (
    etf_id      varchar(10) not null,
    date        date        not null,
    sbl_sell    bigint,
    sbl_return  bigint,
    sbl_adjust  bigint,
    sbl_balance bigint,
    sbl_limit   bigint,
    primary key (etf_id, date)
);
comment on table  public.etf_sbl_daily is 'ETF 借券賣出（股）：上市 TWT93U、上櫃 margin/sbl「信用額度總量管制餘額表」的借券欄位；保留 1 年';
comment on column public.etf_sbl_daily.etf_id      is 'ETF 代號';
comment on column public.etf_sbl_daily.date        is '交易日';
comment on column public.etf_sbl_daily.sbl_sell    is '當日借券賣出（股）';
comment on column public.etf_sbl_daily.sbl_return  is '當日還券（股）';
comment on column public.etf_sbl_daily.sbl_adjust  is '當日調整（股）';
comment on column public.etf_sbl_daily.sbl_balance is '借券賣出當日餘額（股）';
comment on column public.etf_sbl_daily.sbl_limit   is '次一營業日可借券賣出限額（股）';

alter table public.etf_margin_daily enable row level security;
alter table public.etf_top_holdings enable row level security;
alter table public.etf_sbl_daily enable row level security;
