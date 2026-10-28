// LINE 機器人的 webhook：使用者打股票代號或名稱，回覆那檔股票的個股分析卡
//
// 卡片由 line_cards.py 每晚算好放在 Storage（archive 桶 _line_cards/），這裡只讀檔回覆；
// 打「自選」時另外讀資料庫的分組與自選股清單（只讀，不寫入）；
// 「明日買進」「均線買賣點」「動能股」「大盤趨勢」回覆 line_lists.py 每晚算好的清單（picks.json、granville.json、momentum.json、market.json）。
// 用 LINE 的回覆訊息（reply）回答，不算每月推播額度。
//
// 需要的設定（Supabase → Edge Functions → Secrets）：
//   LINE_CHANNEL_SECRET：LINE Developers 後台 Basic settings 的 Channel secret（驗證訊息真的是 LINE 送來的）
//   LINE_CHANNEL_ACCESS_TOKEN：和每日推播相同的 Channel access token
//   LINE_WATCH_ACCOUNTS（選填）：誰打「自選」可以看哪個網頁帳號的自選股，格式「LINE user ID:網頁帳號」，
//     多個人用逗號分開。沒列在這裡的人打「自選」會收到說明；LINE user ID 可以在 LINE 打「我的ID」查。
//     列在這裡的人傳訊息或加好友時，會自動換成有「自選」按鈕的完整選單（line_richmenu.py 建立，別名 watch-full）；
//     其他人用預設的四格選單。
// SUPABASE_URL、SUPABASE_SERVICE_ROLE_KEY 是 Supabase 自動提供的，不用設定。
// 部署時要關掉 JWT 驗證（LINE 不會帶 Supabase 的登入憑證，改用上面的簽章驗證）。

import {
  choicesMessage, type Group, helpText, type Index, lookup, parseAccounts, shardOf, watchlistMessages,
} from "./lookup.ts";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const CHANNEL_SECRET = Deno.env.get("LINE_CHANNEL_SECRET") ?? "";
const ACCESS_TOKEN = Deno.env.get("LINE_CHANNEL_ACCESS_TOKEN") ?? "";
const ACCOUNTS = parseAccounts(Deno.env.get("LINE_WATCH_ACCOUNTS") ?? "");
const CARDS_URL = `${SUPABASE_URL}/storage/v1/object/archive/_line_cards`;
const CACHE_MS = 10 * 60 * 1000; // 卡片每晚才換新，暫存 10 分鐘，連續查詢不必每次讀檔
const FULL_MENU_ALIAS = "watch-full"; // 要和 line_richmenu.py 的 FULL_ALIAS 相同

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

const linked = new Map<string, number>(); // 最近換過完整選單的人，10 分鐘內不重複呼叫 LINE

/** 有對應網頁帳號的人換成有「自選」按鈕的完整選單；失敗只記錄，不影響回覆 */
async function linkFullMenu(userId: string | undefined) {
  if (!userId || !ACCOUNTS[userId]) return;
  const at = linked.get(userId);
  if (at && Date.now() - at < CACHE_MS) return;
  const auth = { Authorization: `Bearer ${ACCESS_TOKEN}` };
  try {
    const alias = await fetch(`https://api.line.me/v2/bot/richmenu/alias/${FULL_MENU_ALIAS}`, { headers: auth });
    if (!alias.ok) throw new Error(`讀取選單別名失敗：${alias.status} ${await alias.text()}`);
    const { richMenuId } = await alias.json();
    const res = await fetch(`https://api.line.me/v2/bot/user/${userId}/richmenu/${richMenuId}`, {
      method: "POST", headers: auth,
    });
    if (!res.ok) throw new Error(`換完整選單失敗：${res.status} ${await res.text()}`);
    await res.body?.cancel();
    linked.set(userId, Date.now());
  } catch (e) {
    console.error(e);
  }
}

async function select<T>(path: string): Promise<T[]> {
  const res = await fetch(`${SUPABASE_URL}/rest/v1/${path}`, {
    headers: { Authorization: `Bearer ${SERVICE_KEY}`, apikey: SERVICE_KEY },
  });
  if (!res.ok) throw new Error(`查詢 ${path.split("?")[0]} 失敗：${res.status} ${await res.text()}`);
  return await res.json();
}

/** 網頁帳號的分組與股票（依分組建立順序、股票加入順序；空的分組不列，同 line_push.load_groups） */
async function loadGroups(owner: string): Promise<Group[]> {
  const groups = await select<{ id: number; name: string }>(
    `watch_groups?select=id,name&owner=eq.${encodeURIComponent(owner)}&order=id`,
  );
  if (!groups.length) return [];
  const rows = await select<{ group_id: number; stock_id: string }>(
    `watchlist?select=group_id,stock_id&group_id=in.(${groups.map((g) => g.id).join(",")})&order=added_at`,
  );
  return groups
    .map((g) => ({ name: g.name, ids: rows.filter((r) => r.group_id === g.id).map((r) => r.stock_id) }))
    .filter((g) => g.ids.length);
}

async function watchlist(userId: string | undefined, index: Index): Promise<unknown[]> {
  const owner = userId ? ACCOUNTS[userId] : undefined;
  if (!owner) {
    return [text("你的 LINE 還沒有對應到網頁帳號，所以看不到自選股。\n請把下面這串 LINE user ID 給管理者，加到 Supabase 的 LINE_WATCH_ACCOUNTS 設定：\n" + (userId ?? "（查不到）"))];
  }
  const groups = await loadGroups(owner);
  if (!groups.length) return [text(`帳號 ${owner} 目前沒有自選股，請先到網頁「自選股」頁加入。`)];
  const ids = [...new Set(groups.flatMap((g) => g.ids))];
  const shards = await Promise.all(
    [...new Set(ids.map(shardOf))].map((k) => readJson<Record<string, unknown>>(`${k}.json`)),
  );
  const cards: Record<string, unknown> = {};
  for (const shard of shards) {
    for (const id of ids) if (shard?.[id]) cards[id] = shard[id];
  }
  return watchlistMessages(groups, index, cards);
}

async function answer(input: string, userId?: string): Promise<unknown[]> {
  const index = await readJson<Index>("index.json");
  if (!index) return [text("分析卡還沒產生，請等今晚收盤資料更新後再試。")];
  const r = lookup(input, index);
  if (r.kind === "help") return [text(helpText(index))];
  if (r.kind === "myid") return [text(userId ? `你的 LINE user ID：\n${userId}` : "查不到你的 LINE user ID（群組裡請私訊機器人查）")];
  if (r.kind === "watchlist") return await watchlist(userId, index);
  if (r.kind === "list") {
    const list = await readJson<{ date: string | null; messages: unknown[] }>(r.file);
    return list?.messages?.length ? list.messages : [text("清單還沒產生，請等今晚收盤資料更新後再試。")];
  }
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
      await linkFullMenu(ev.source?.userId);
    } else if (ev.type === "message" && ev.message?.type === "text" && ev.replyToken) {
      try {
        await reply(ev.replyToken, await answer(ev.message.text, ev.source?.userId));
      } catch (e) {
        console.error(e);
        await reply(ev.replyToken, [text("查詢時發生錯誤，請稍後再試。")]);
      }
      await linkFullMenu(ev.source?.userId);
    }
  }
  return new Response("ok");
});
