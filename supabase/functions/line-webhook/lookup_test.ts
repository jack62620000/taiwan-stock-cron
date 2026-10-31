// 執行：node --test supabase/functions/line-webhook/lookup_test.ts（Node 22 以上；PR 自動測試也會跑）
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  briefMessages, choicesMessage, lookup, MAX_CHOICES, MESSAGES_PER_REPLY, packCarousels, parseAccounts, watchlistMessages,
} from "./lookup.ts";

const index = {
  date: "2026-10-20",
  stocks: { "2330": "台積電", "2303": "聯電", "2317": "鴻海", "2454": "聯發科", "00878": "國泰永續高股息", "2881": "富邦金", "2882": "國泰金" },
};

test("代號", () => {
  assert.deepEqual(lookup(" 2330 ", index), { kind: "stock", id: "2330" });
  assert.deepEqual(lookup("00878", index), { kind: "stock", id: "00878" });
});

test("名稱", () => {
  assert.deepEqual(lookup("台積電", index), { kind: "stock", id: "2330" });
  assert.deepEqual(lookup("鴻 海", index), { kind: "stock", id: "2317" });
  assert.deepEqual(lookup("聯發", index), { kind: "stock", id: "2454" });
});

test("對到好幾檔時列出來選", () => {
  assert.deepEqual(lookup("聯", index), { kind: "choices", ids: ["2303", "2454"] });
  assert.deepEqual(lookup("國泰", index), { kind: "choices", ids: ["00878", "2882"] });
  assert.deepEqual(lookup("23", index), { kind: "choices", ids: ["2303", "2317", "2330"] });
  const many = { date: "x", stocks: Object.fromEntries([...Array(30)].map((_, k) => [`${1100 + k}`, `測試${k}`])) };
  const r = lookup("測試", many);
  assert.equal(r.kind === "choices" && r.ids.length, MAX_CHOICES);
  const msg = choicesMessage(["2303", "2454"], index);
  assert.equal(msg.quickReply.items[0].action.text, "2303");
});

test("說明與查不到", () => {
  assert.deepEqual(lookup("說明", index), { kind: "help" });
  assert.deepEqual(lookup("HELP", index), { kind: "help" });
  assert.deepEqual(lookup("蘋果", index), { kind: "none", query: "蘋果" });
});

test("自選與我的ID", () => {
  assert.deepEqual(lookup("自選", index), { kind: "watchlist" });
  assert.deepEqual(lookup("自選股", index), { kind: "watchlist" });
  assert.deepEqual(lookup("我的ID", index), { kind: "myid" });
  assert.deepEqual(lookup("葛蘭碧", index), { kind: "list", file: "granville.json" });
  assert.deepEqual(lookup("均線買賣點", index), { kind: "list", file: "granville.json" });
  assert.deepEqual(lookup("明日買進", index), { kind: "list", file: "picks.json" });
  assert.deepEqual(lookup("動能股", index), { kind: "list", file: "momentum.json" });
  assert.deepEqual(lookup("大盤趨勢", index), { kind: "list", file: "market.json" });
  assert.deepEqual(lookup("早報", index), { kind: "list", file: "brief.json" });
  assert.deepEqual(lookup("週報", index), { kind: "list", file: "brief.json" });
});

test("帳號設定", () => {
  assert.deepEqual(parseAccounts("U1:jack, U2:mom\nU3 : dad"), { U1: "jack", U2: "mom", U3: "dad" });
  assert.deepEqual(parseAccounts(""), {});
});

test("自選股回覆：文字＋輪播，超過一次回覆的量列在文字裡", () => {
  const big = { type: "bubble", body: { type: "box", layout: "vertical", contents: [{ type: "text", text: "x".repeat(14_000) }] } };
  const ids = [...Array(16)].map((_, k) => `${3000 + k}`);
  const idx = { date: "2026-10-20", stocks: Object.fromEntries(ids.map((id) => [id, `股${id}`])) };
  const cards = Object.fromEntries(ids.slice(0, 15).map((id) => [id, big]));
  const msgs = watchlistMessages([{ name: "A", ids: ids.slice(0, 10) }, { name: "B", ids: ids.slice(5) }], idx, cards);
  assert.ok(msgs.length <= MESSAGES_PER_REPLY);
  const carousels = msgs.slice(1) as Array<{ contents: { contents: unknown[] } }>;
  assert.equal(carousels.reduce((n, m) => n + m.contents.contents.length, 0), 12); // 每則 3 張 × 4 則
  for (const m of carousels) assert.ok(new TextEncoder().encode(JSON.stringify(m.contents)).length <= 45_000);
  const t = (msgs[0] as { text: string }).text;
  assert.match(t, /【A】/);
  assert.match(t, /沒有分析卡：3015 股3015/);
  assert.match(t, /請直接輸入代號查：3012 股3012、3013 股3013、3014 股3014/);
  assert.equal(packCarousels([1, 2, 3]).length, 1);
});

test("早報：文字加圖片，最多 4 張，沒有連結的圖片不送", () => {
  const images = [1, 2, 3, 4, 5].map((k) => ({ path: `brief/${k}.jpg`, preview: `brief/p${k}.jpg` }));
  const signed = Object.fromEntries(images.flatMap((im) => [[im.path, `https://x/${im.path}`], [im.preview, `https://x/${im.preview}`]]));
  delete signed["brief/p2.jpg"];
  const brief = { date: "2026-10-31", title: "台股週報 10/31（六）", headline: "費半重挫", url: "https://claude.ai/artifact/x", images };
  const msgs = briefMessages(brief, signed) as Array<Record<string, string>>;
  assert.equal(msgs.length, MESSAGES_PER_REPLY);
  assert.equal(msgs[0].type, "text");
  assert.ok(msgs[0].text.includes("完整早報：https://claude.ai/artifact/x"));
  assert.deepEqual(msgs.slice(1).map((m) => m.originalContentUrl), ["https://x/brief/1.jpg", "https://x/brief/3.jpg", "https://x/brief/4.jpg", "https://x/brief/5.jpg"]);
  assert.equal(msgs[1].previewImageUrl, "https://x/brief/p1.jpg");
  assert.equal((briefMessages({ ...brief, url: null }, {})[0] as Record<string, string>).text.includes("完整早報"), false);
});

