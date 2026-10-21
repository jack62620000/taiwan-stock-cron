// LINE 機器人的 webhook：使用者打股票代號或名稱，回覆那檔股票的個股分析卡
//
// 卡片由 line_cards.py 每晚算好放在 Storage（archive 桶 _line_cards/），這裡只讀檔回覆，不查資料庫。
// 用 LINE 的回覆訊息（reply）回答，不算每月推播額度。
//
// 需要的設定（Supabase → Edge Functions → Secrets）：
//   LINE_CHANNEL_SECRET：LINE Developers 後台 Basic settings 的 Channel secret（驗證訊息真的是 LINE 送來的）
//   LINE_CHANNEL_ACCESS_TOKEN：和每日推播相同的 Channel access token
// SUPABASE_URL、SUPABASE_SERVICE_ROLE_KEY 是 Supabase 自動提供的，不用設定。
// 部署時要關掉 JWT 驗證（LINE 不會帶 Supabase 的登入憑證，改用上面的簽章驗證）。

import { choicesMessage, helpText, type Index, lookup, shardOf } from "./lookup.ts";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const CHANNEL_SECRET = Deno.env.get("LINE_CHANNEL_SECRET") ?? "";
const ACCESS_TOKEN = Deno.env.get("LINE_CHANNEL_ACCESS_TOKEN") ?? "";
const CARDS_URL = `${SUPABASE_URL}/storage/v1/object/archive/_line_cards`;
const CACHE_MS = 10 * 60 * 1000; // 卡片每晚才換新，暫存 10 分鐘，連續查詢不必每次讀檔

const cache = new Map<string, { time: number; data: unknown }>();

async function readJson<T>(name: string): Promise<T | null> {
  const hit = cache.get(name);
  if (hit && Date.now() - hit.time < CACHE_MS) return hit.data as T;
  const res = await fetch(`${CARDS_URL}/${name}`, {
    headers: { Authorization: `Bearer ${SERVICE_KEY}`, apikey: SERVICE_KEY },
  });
  if (!res.ok) {
    await res.body?.cancel();
    if (res.status === 400 || res.status === 404) return null; // 檔案不存在
    throw new Error(`讀取 ${name} 失敗：${res.status}`);
  }
  const data = await res.json();
  cache.set(name, { time: Date.now(), data });
  return data as T;
}

async function verify(body: string, signature: string | null): Promise<boolean> {
  if (!CHANNEL_SECRET || !signature) return false;
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(CHANNEL_SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["verify"],
  );
  let sig: Uint8Array;
  try {
    sig = Uint8Array.from(atob(signature), (c) => c.charCodeAt(0));
  } catch {
    return false;
  }
  return crypto.subtle.verify("HMAC", key, sig, new TextEncoder().encode(body));
}

async function reply(replyToken: string, messages: unknown[]) {
  const res = await fetch("https://api.line.me/v2/bot/message/reply", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${ACCESS_TOKEN}` },
    body: JSON.stringify({ replyToken, messages }),
  });
  if (!res.ok) console.error(`LINE 回覆失敗：${res.status} ${await res.text()}`);
  else await res.body?.cancel();
}

const text = (t: string) => ({ type: "text", text: t });

async function answer(input: string): Promise<unknown[]> {
  const index = await readJson<Index>("index.json");
  if (!index) return [text("分析卡還沒產生，請等今晚收盤資料更新後再試。")];
  const r = lookup(input, index);
  if (r.kind === "help") return [text(helpText(index))];
  if (r.kind === "none") return [text(`找不到「${r.query}」，請輸入股票代號（例如 2330）或名稱（例如 台積電）。`)];
  if (r.kind === "choices") return [choicesMessage(r.ids, index)];
  const shard = await readJson<Record<string, unknown>>(`${shardOf(r.id)}.json`);
  const card = shard?.[r.id];
  if (!card) return [text(`${r.id} 目前沒有分析卡資料。`)];
  return [{ type: "flex", altText: `${r.id} ${index.stocks[r.id]} 個股分析卡`, contents: card }];
}

Deno.serve(async (req) => {
  if (req.method !== "POST") return new Response("ok");
  const body = await req.text();
  if (!(await verify(body, req.headers.get("x-line-signature")))) {
    return new Response("invalid signature", { status: 401 });
  }
  const events = (JSON.parse(body).events ?? []) as Array<Record<string, any>>;
  for (const ev of events) {
    if (ev.type === "follow" && ev.replyToken) {
      await reply(ev.replyToken, [text(helpText(await readJson<Index>("index.json").catch(() => null)))]);
    } else if (ev.type === "message" && ev.message?.type === "text" && ev.replyToken) {
      try {
        await reply(ev.replyToken, await answer(ev.message.text));
      } catch (e) {
        console.error(e);
        await reply(ev.replyToken, [text("查詢時發生錯誤，請稍後再試。")]);
      }
    }
  }
  return new Response("ok");
});
