// 執行：node --test supabase/functions/line-webhook/lookup_test.ts（或 deno test）
import assert from "node:assert/strict";
import { test } from "node:test";
import { choicesMessage, lookup, MAX_CHOICES } from "./lookup.ts";

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
