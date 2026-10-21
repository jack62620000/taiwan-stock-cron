// LINE 打字查詢：把使用者打的字對應到股票（只有字串處理，方便單獨測試）

export type Index = { date: string; stocks: Record<string, string> };

export type Lookup =
  | { kind: "help" }
  | { kind: "watchlist" }
  | { kind: "myid" }
  | { kind: "list"; file: string }
  | { kind: "stock"; id: string }
  | { kind: "choices"; ids: string[] }
  | { kind: "none"; query: string };

const HELP_WORDS = new Set(["說明", "help", "?", "？", "使用說明", "幫助"]);
const WATCH_WORDS = new Set(["自選", "自選股", "我的自選股"]);
const MYID_WORDS = new Set(["我的id", "myid", "id"]);
// 選單清單：每晚由 line_lists.py 算好的回覆檔
const LIST_WORDS: Record<string, string> = {
  "葛蘭碧": "granville.json", "等待法則2": "granville.json",
  "動能": "momentum.json", "動能股": "momentum.json", "動能選股": "momentum.json",
};
export const MAX_CHOICES = 10; // 名稱對到很多檔時，最多列幾檔讓使用者點選（LINE 快速回覆最多 13 個）

export function lookup(text: string, index: Index): Lookup {
  const q = text.trim().replace(/\s+/g, "");
  if (!q || HELP_WORDS.has(q.toLowerCase())) return { kind: "help" };
  if (WATCH_WORDS.has(q)) return { kind: "watchlist" };
  if (MYID_WORDS.has(q.toLowerCase())) return { kind: "myid" };
  if (q in LIST_WORDS) return { kind: "list", file: LIST_WORDS[q] };
  const stocks = index.stocks;
  const code = q.toUpperCase();
  if (code in stocks) return { kind: "stock", id: code };
  // 名稱完全相同 → 開頭相同 → 包含
  const ids = Object.keys(stocks).sort();
  const exact = ids.filter((id) => stocks[id] === q);
  if (exact.length === 1) return { kind: "stock", id: exact[0] };
  const starts = ids.filter((id) => stocks[id].startsWith(q));
  const contains = ids.filter((id) => !starts.includes(id) && stocks[id].includes(q));
  const found = [...exact, ...starts.filter((id) => !exact.includes(id)), ...contains];
  // 代號只打前幾碼（例如 233）
  if (!found.length && /^\d{2,5}$/.test(q)) found.push(...ids.filter((id) => id.startsWith(q)));
  if (found.length === 1) return { kind: "stock", id: found[0] };
  if (found.length) return { kind: "choices", ids: found.slice(0, MAX_CHOICES) };
  return { kind: "none", query: q };
}

export function shardOf(id: string): string {
  return id.slice(0, 2);
}

export function helpText(index: Index | null): string {
  const lines = [
    "📊 個股分析卡查詢",
    "直接輸入股票代號或名稱，例如：",
    "2330",
    "台積電",
    "",
    "輸入「自選」會回覆你網頁自選股每一檔的分析卡（要先對應網頁帳號：輸入「我的ID」，把那串 ID 給管理者）。",
    "輸入「葛蘭碧」會列出等待法則 2 的股票；輸入「動能股」會列出動能選股。",
    "",
    "會回覆和網頁「個股分析卡」相同的分析（52 週區間、基本面、營收、籌碼、資金、技術面、葛蘭碧、每日評分、動能）。",
  ];
  if (index) lines.push("", `資料日期：${index.date}（每晚收盤資料更新後換新）`);
  return lines.join("\n");
}

export function choicesMessage(ids: string[], index: Index) {
  return {
    type: "text",
    text: `找到好幾檔，請點選或直接輸入代號：\n` + ids.map((id) => `${id} ${index.stocks[id]}`).join("\n"),
    quickReply: {
      items: ids.map((id) => ({
        type: "action",
        action: { type: "message", label: `${id} ${index.stocks[id]}`.slice(0, 20), text: id },
      })),
    },
  };
}

// ===== 自選股 =====
export const MESSAGES_PER_REPLY = 5; // 一次回覆最多 5 則訊息
export const BUBBLES_PER_CAROUSEL = 12; // 一則輪播最多 12 張卡片
export const CAROUSEL_MAX_BYTES = 45_000; // 一則輪播的 JSON 上限約 50 KB，留一點餘裕（同 line_push.py）

/** LINE_WATCH_ACCOUNTS 設定：「LINE user ID:網頁帳號」，多組用逗號或換行分開 */
export function parseAccounts(setting: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const part of setting.split(/[,\n]/)) {
    const i = part.indexOf(":");
    if (i < 0) continue;
    const uid = part.slice(0, i).trim();
    const owner = part.slice(i + 1).trim();
    if (uid && owner) out[uid] = owner;
  }
  return out;
}

const bytes = (v: unknown) => new TextEncoder().encode(JSON.stringify(v)).length;

/** 把卡片分成好幾則輪播：每則最多 12 張、不超過大小上限 */
export function packCarousels(bubbles: unknown[]): unknown[][] {
  const out: unknown[][] = [];
  let cur: unknown[] = [];
  for (const b of bubbles) {
    const next = [...cur, b];
    if (cur.length && (next.length > BUBBLES_PER_CAROUSEL || bytes({ type: "carousel", contents: next }) > CAROUSEL_MAX_BYTES)) {
      out.push(cur);
      cur = [b];
    } else {
      cur = next;
    }
  }
  if (cur.length) out.push(cur);
  return out;
}

export type Group = { name: string; ids: string[] };

/**
 * 自選股的回覆：第一則文字列出各分組的股票，後面是分析卡輪播（最多 4 則）。
 * 放不下的股票寫在文字裡，請使用者直接輸入代號查。
 */
export function watchlistMessages(groups: Group[], index: Index, cards: Record<string, unknown>) {
  const ids = [...new Set(groups.flatMap((g) => g.ids))];
  const withCard = ids.filter((id) => cards[id]);
  const carousels = packCarousels(withCard.map((id) => cards[id])).slice(0, MESSAGES_PER_REPLY - 1);
  const shown = new Set(withCard.slice(0, carousels.reduce((n, c) => n + c.length, 0)));
  const name = (id: string) => `${id} ${index.stocks[id] ?? ""}`.trim();
  const lines = [`📋 自選股分析（${index.date}）`];
  for (const g of groups) lines.push("", `【${g.name}】`, g.ids.map(name).join("、"));
  const noCard = ids.filter((id) => !cards[id]);
  if (noCard.length) lines.push("", `沒有分析卡：${noCard.map(name).join("、")}`);
  const rest = withCard.filter((id) => !shown.has(id));
  if (rest.length) lines.push("", `一次回覆放不下，請直接輸入代號查：${rest.map(name).join("、")}`);
  return [
    { type: "text", text: lines.join("\n") },
    ...carousels.map((c) => ({ type: "flex", altText: "自選股分析卡", contents: { type: "carousel", contents: c } })),
  ];
}
