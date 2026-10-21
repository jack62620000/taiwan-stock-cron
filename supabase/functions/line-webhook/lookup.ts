// LINE 打字查詢：把使用者打的字對應到股票（只有字串處理，方便單獨測試）

export type Index = { date: string; stocks: Record<string, string> };

export type Lookup =
  | { kind: "help" }
  | { kind: "stock"; id: string }
  | { kind: "choices"; ids: string[] }
  | { kind: "none"; query: string };

const HELP_WORDS = new Set(["說明", "help", "?", "？", "使用說明", "幫助"]);
export const MAX_CHOICES = 10; // 名稱對到很多檔時，最多列幾檔讓使用者點選（LINE 快速回覆最多 13 個）

export function lookup(text: string, index: Index): Lookup {
  const q = text.trim().replace(/\s+/g, "");
  if (!q || HELP_WORDS.has(q.toLowerCase())) return { kind: "help" };
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
