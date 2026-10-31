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
  "明日買進": "picks.json", "買進": "picks.json", "選股": "picks.json",
  "均線買賣點": "granville.json", "均線": "granville.json", "葛蘭碧": "granville.json", "等待法則2": "granville.json",
  "動能": "momentum.json", "動能股": "momentum.json", "動能選股": "momentum.json",
  "大盤趨勢": "market.json", "大盤": "market.json",
  // 每日台股早報的摘要卡：每天早上由 line_brief.py 產生
  "早報": "brief.json", "台股早報": "brief.json", "週報": "brief.json", "晨報": "brief.json",
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
    "會回覆和網頁「個股分析卡」相同的分析（52 週區間、基本面、營收、籌碼、資金、技術面、均線買賣點、評分排行、動能）。",
    "",
    "以下清單都只看 20 日均量 500 張以上的股票，每晚收盤資料更新後換新。",
    "",
    "【⭐ 自選股】",
    "回覆你網頁自選股每一檔的分析卡。要先對應網頁帳號：輸入「我的ID」，把那串 ID 給管理者。",
    "",
    "【🛒 明日買進】",
    "每天幫全部股票打分數（滿分 100），列出總分前 10 名：",
    "・籌碼 32 分：外資 5 日買超 4、買超佔成交量 ≥5% 再 3、外資連買 ≥3 天 3；投信 5 日買超 7、投信連買 ≥3 天 8；大戶持股 4 週增 ≥1 個百分點 7",
    "・資金 8 分：券資比 ≥10% 得 8、≥5% 得 4",
    "・基本面 35 分：月營收年增 >0 得 4（≥20% 得 8）、累計營收年增 >0 得 4、營收連 3 月年增 10、營收創近期新高 13",
    "・技術 25 分：站上月線 10、近 20 日上漲 11、RSI ≥70 得 4",
    "買賣規則：加權指數收盤同時在 20 日均線（月線）和 60 日均線（季線）之上才買；隔天開盤買進，持有 10 個交易日後開盤賣出。",
    "",
    "【📐 均線買賣點】（葛蘭碧八大法則，20 日均線）",
    "・起點（法則 1）：均線由下彎轉為走平或上揚，收盤由下往上突破均線；同時外資 5 日買超、MACD 柱 >0、當天成交量 >1.5 倍均量",
    "・等待法則 2：起點後 20 個交易日內，均線持續上揚（且月線在季線之上），股價短暫跌破均線後又站回，就是買點；回測是隔天開盤買、持有 5～8 個交易日",
    "・清單列出起點後還在等法則 2 的股票，以及明天收盤到多少會出現法則 2（觸發價）",
    "・急跌反彈（法則 4）：均線下彎，收盤低於均線 10% 以上；回測勝率約四成多，一定要設停損",
    "",
    "【🚀 動能股】",
    "・趨勢模板：收盤在 150、200 日均線之上；150 日線在 200 日線之上；200 日線比一個月前高；50 日線在 150、200 日線之上；收盤在 50 日線之上；比 52 週低點高 30% 以上；在 52 週高點 25% 以內；相對強度 RS ≥70。8 個全部符合才算",
    "・52 週新高＋量增：收盤創 52 週新高，成交量至少是前 50 日均量的 1.5 倍",
    "・趨勢中回檔再起：符合趨勢模板，前 10 個交易日碰過布林下軌，今天站上布林中軌（20 日均線）",
    "・兩者同時：同時符合趨勢模板和 52 週新高＋量增；今天剛符合趨勢模板：今天才 8 個條件全部符合",
    "・離 50 日均線 30% 以上的漲多股不列入",
    "",
    "【🧭 大盤趨勢】",
    "和網頁首頁的大盤趨勢相同：加權指數在哪幾條均線之上或之下、月線和季線的方向、距高點多遠、量能、外資現股買賣超金額、外資台指期淨未平倉的增減、上下最近的支撐壓力，以及「明日買進」的大盤濾網是綠燈還是紅燈。",
    "",
    "【📰 早報】",
    "每天早上 8 點前更新：美股收盤、台積電 ADR、台股前一天大盤與法人、關注股的轉強轉弱價位和今天怎麼看；週六是週報。卡片下方按鈕可以打開完整早報。",
    "",
    "以上僅供參考，不構成投資建議。",
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
