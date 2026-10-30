-- 主資料庫（fewuioenftajlahumcve）：用 pg_cron 準時觸發 GitHub Actions
-- GitHub 自己的 schedule 常延誤 2～6 小時；workflow_dispatch 一分鐘內就會開始跑。
-- 各 workflow 原本的 schedule 留著當備援（資料都是 upsert，重複跑不會重複寫入）。
--
-- 金鑰不放在這裡：GitHub fine-grained token（只選 taiwan-stock-cron、權限只有 Actions: Read and write），
-- 由使用者在 Supabase SQL Editor 自己存進 Vault：
--   select vault.create_secret('<token>', 'github_dispatch_token');
-- 檢查觸發結果：select id, status_code, content from net._http_response order by id desc limit 10;（204 為成功）

create extension if not exists pg_cron;
create extension if not exists pg_net;

-- 不對外開放的 schema：PostgREST 只開放 public，網站與 LINE 機器人都碰不到
create schema if not exists ops;
revoke all on schema ops from public, anon, authenticated;

create or replace function ops.dispatch_workflow(workflow text, inputs jsonb default '{}'::jsonb)
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
  token text;
begin
  select decrypted_secret into token from vault.decrypted_secrets where name = 'github_dispatch_token';
  if token is null then
    raise exception 'Vault 找不到 github_dispatch_token';
  end if;
  return net.http_post(
    url := 'https://api.github.com/repos/jack62620000/taiwan-stock-cron/actions/workflows/' || workflow || '/dispatches',
    headers := jsonb_build_object(
      'Authorization', 'Bearer ' || token,
      'Accept', 'application/vnd.github+json',
      'X-GitHub-Api-Version', '2022-11-28',
      'User-Agent', 'supabase-pg-cron'),
    body := jsonb_build_object('ref', 'main', 'inputs', inputs)
  );
end;
$$;
revoke all on function ops.dispatch_workflow(text, jsonb) from public, anon, authenticated;

-- 時間皆為 UTC，和各 workflow 原本的 GitHub schedule 相同（台灣時間 = UTC + 8）
select cron.schedule('gh-futures',      '8 13 * * 1-5',       $$select ops.dispatch_workflow('daily_update.yml', '{"task":"futures"}')$$);
select cron.schedule('gh-daily',        '11 15 * * 1-5',      $$select ops.dispatch_workflow('daily_update.yml', '{"task":"daily"}')$$);
select cron.schedule('gh-morning',      '30 22 * * 1-5',      $$select ops.dispatch_workflow('daily_update.yml', '{"task":"morning"}')$$);
select cron.schedule('gh-holder',       '6 1 * * 6',          $$select ops.dispatch_workflow('daily_update.yml', '{"task":"holder"}')$$);
select cron.schedule('gh-etf-daily',    '40 10 * * 1-5',      $$select ops.dispatch_workflow('etf_update.yml', '{"job":"daily"}')$$);
select cron.schedule('gh-etf-weekly',   '36 1 * * 6',         $$select ops.dispatch_workflow('etf_update.yml', '{"job":"weekly"}')$$);
select cron.schedule('gh-etf-monthly',  '16 2 10,15,25 * *',  $$select ops.dispatch_workflow('etf_update.yml', '{"job":"monthly"}')$$);
select cron.schedule('gh-us-market-1',  '10 21 * * 1-5',      $$select ops.dispatch_workflow('us_market.yml')$$);
select cron.schedule('gh-us-market-2',  '40 22 * * 1-5',      $$select ops.dispatch_workflow('us_market.yml')$$);
select cron.schedule('gh-quarterly',    '6 2 * * 6',          $$select ops.dispatch_workflow('quarterly_update.yml')$$);
select cron.schedule('gh-verify-public','0 4 * * 0',          $$select ops.dispatch_workflow('verify_public.yml')$$);
select cron.schedule('gh-db-backup',    '20 19 * * 6',        $$select ops.dispatch_workflow('db_backup.yml')$$);
