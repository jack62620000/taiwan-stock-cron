-- 2026-10-19 家人各自帳號：自選股、模擬持股分組
-- 每個登入帳號（owner）底下可以有多個分組；自選股、模擬持股都屬於某一個分組。
-- 原本的資料放進 owner 為空字串的「預設」分組，主帳號（secrets 的 USERNAME）第一次登入時認領。
create table if not exists public.watch_groups (
    id         bigint      generated always as identity primary key,
    owner      text        not null,
    name       text        not null check (length(btrim(name)) > 0),
    created_at timestamptz not null default now(),
    unique (owner, name)
);

insert into public.watch_groups (owner, name)
select '', '預設'
where not exists (select 1 from public.watch_groups where owner = '' and name = '預設');

-- 自選股：加分組欄位，同一檔股票可以在不同分組，主鍵改成 (group_id, stock_id)
alter table public.watchlist add column if not exists group_id bigint
    references public.watch_groups(id) on delete cascade;
update public.watchlist set group_id = (select id from public.watch_groups where owner = '' and name = '預設')
where group_id is null;
alter table public.watchlist alter column group_id set not null;
alter table public.watchlist drop constraint if exists watchlist_pkey;
alter table public.watchlist add primary key (group_id, stock_id);

-- 模擬持股：加分組欄位
alter table public.paper_trades add column if not exists group_id bigint
    references public.watch_groups(id) on delete cascade;
update public.paper_trades set group_id = (select id from public.watch_groups where owner = '' and name = '預設')
where group_id is null;
alter table public.paper_trades alter column group_id set not null;
create index if not exists paper_trades_group_idx on public.paper_trades (group_id);
