-- 自選股：網頁「⭐ 自選股」頁和個股分析卡的加入／移除按鈕讀寫這張表
-- 2026-10-18 建立
create table if not exists public.watchlist (
    stock_id  varchar     primary key references public.stock_info(stock_id),
    note      text        not null default '',
    added_at  timestamptz not null default now()
);
