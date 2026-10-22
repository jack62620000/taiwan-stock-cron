"""
LINE 選單「明日買進」「均線買賣點」「動能股」的回覆 - 和網頁同一套計算，每晚由 line_cards.py 算好存到 Storage

    明日買進：選股頁「明日買進」的評分前 10 名與參考標籤（utils.picks.buy_list），大盤在季線下時提醒先不買
    均線買賣點：選股頁「均線買賣點」（葛蘭碧）「買賣預告 → 準備買進」中狀態為「⏳ 等待法則2」的股票（網頁預設參數、不加篩選），
            欄位、數字、說明文字和網頁表格相同（utils.analysis.granville_buy_table）；
            後面接「急跌反彈候選」（法則 4，utils.analysis.granville_rebound_table），最多一則輪播
    動能股：選股頁「評分排行」裡「動能選股」的「兩者同時」「52 週新高＋量增」「今天剛符合趨勢模板」三種，
            欄位和網頁表格相同（utils.momentum.momentum_table）

每檔一張小卡，卡片下方的按鈕可以直接查那檔的完整個股分析卡。
存放：archive 桶 / _line_cards / picks.json、granville.json、momentum.json，內容是 {"date": 資料日期, "messages": [LINE 訊息]}，
LINE 機器人（supabase/functions/line-webhook）收到時原樣回覆，最多 5 則訊息。
"""
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app"))

from line_push import GOLD, GRAY, _carousels, _f, _num, _row, _sign_color, _text  # noqa: E402

MAX_MESSAGES = 5          # 一次回覆最多 5 則訊息
TEXT_MAX = 4900           # 文字訊息上限 5,000 字
MOMENTUM_PICKS = ["兩者同時", "趨勢中回檔再起", "52 週新高＋量增", "今天剛符合趨勢模板"]


def _bubble(sid, name, industry, tag, rows, note=None):
    """一檔股票的小卡：標題（代號、名稱、產業、狀態）、欄位列、說明文字，下方按鈕查完整分析卡"""
    body = list(rows)
    if note:
        body += [{"type": "separator", "margin": "md"}, _text(note, size="xs", color=GRAY, margin="md")]
    return {
        "type": "bubble", "size": "kilo",
        "header": {"type": "box", "layout": "vertical", "contents": [
            _text(tag, size="xs", color=GOLD, bold=True),
            _text(f"{sid} {name}", size="lg", bold=True),
            _text(industry or "", size="xs", color=GRAY),
        ]},
        "body": {"type": "box", "layout": "vertical", "spacing": "xs", "contents": body},
        "footer": {"type": "box", "layout": "vertical", "contents": [
            {"type": "button", "style": "link", "height": "sm",
             "action": {"type": "message", "label": "看完整分析卡", "text": sid}},
        ]},
    }


def _pack(title, bubbles, max_carousels):
    """卡片打包成輪播（最多 max_carousels 則），回傳 (訊息, 放不下的卡片數)"""
    chunks = _carousels(bubbles)
    shown = chunks[:max_carousels]
    msgs = [{"type": "flex", "altText": title[:400], "contents": {"type": "carousel", "contents": c}} for c in shown]
    return msgs, len(bubbles) - sum(len(c) for c in shown)


def _rest_line(df, n_rest, id_col, name_col):
    if n_rest <= 0:
        return []
    rest = df.tail(n_rest)
    return ["", "一次回覆放不下，其餘請直接輸入代號查：" + "、".join(f"{r[id_col]} {r[name_col]}" for _, r in rest.iterrows())]


# ===== 葛蘭碧：等待法則2 =====
def granville_bubble(r):
    trig = _num(r["觸發價"])
    rows = [
        _row("最新收盤", _f(r["最新收盤"])),
        _row("觸發價", _f(trig)),
        _row("距離%", _f(r["距離%"], "%") if trig is not None else "N/A", _sign_color(r["距離%"])),
        _row("停損價", _f(r["停損價"])),
        _row("每張風險", _f(r["每張風險"], " 元", 0)),
        _row("比大盤%", _f(r["比大盤%"], "%"), _sign_color(r["比大盤%"])),
        _row("趨勢", f"{r['趨勢']}（穿越均線 {int(r['穿越均線'])} 次）"),
        _row("法則1日期", str(r["法則1日期"])[:10]),
    ]
    return _bubble(r["代號"], r["名稱"], r["產業"], r["狀態"], rows, note=str(r["說明"]))


def rebound_bubble(r):
    rows = [
        _row("最新收盤", _f(r["最新收盤"])),
        _row("均線", _f(r["均線"])),
        _row("乖離%", _f(r["乖離%"], "%"), _sign_color(r["乖離%"])),
        _row("比大盤%", _f(r["比大盤%"], "%"), _sign_color(r["比大盤%"])),
        _row("20日均量", _f(r["20日均量"], " 張", 0)),
    ]
    return _bubble(r["代號"], r["名稱"], r["產業"], "🔵 急跌反彈候選（法則4）", rows,
                   note="跌到離均線很遠，回測常有反彈，但勝率只有四成多，買之前一定要設停損")


def granville_messages(date, table, rebound=None):
    """葛蘭碧回覆：第一則文字（說明與欄位解釋），之後每檔一張卡；最後一則輪播是急跌反彈候選（有的話）"""
    from utils.analysis import GRANVILLE_DEFAULTS, GRANVILLE_MIN_VOLUME
    from utils.granville import CHOP_CROSSES, CHOP_DAYS, HOLD_HINT, RS_DAYS, STOP_RULE, WATCH_DAYS
    waiting = table[table["狀態"] == "⏳ 等待法則2"] if not table.empty else table
    rebound = rebound if rebound is not None else pd.DataFrame()
    lo, hi = HOLD_HINT
    lines = [
        f"📐 均線買賣點（葛蘭碧）：等待法則2（{date} 收盤後）",
        f"共 {len(waiting)} 檔。同網頁選股頁「均線買賣點」的「準備買進」，預設參數（20 日均線、20 日均量 ≥ {GRANVILLE_MIN_VOLUME} 張）。",
        "",
        f"先出現法則 1，之後回檔再站回均線就是法則 2 的買點；回測是法則 2 出現隔天開盤買進、持有 {lo}～{hi} 個交易日。"
        f"法則 1 出現後 {WATCH_DAYS} 個交易日內沒等到法則 2 就失效。",
        "",
        "【欄位說明】",
        "・觸發價：明天收盤到這個價位就出現法則 2",
        "・距離%：觸發價比今天收盤高（+）或低（−）幾 %",
        f"・停損價：{STOP_RULE}，收盤跌破就出場",
        "・每張風險：（預計買進價 − 停損價）× 1000 股，單位：元",
        f"・比大盤%：近 {RS_DAYS} 個交易日漲跌幅減去全部股票平均，正的代表比大盤強",
        f"・趨勢：近 {CHOP_DAYS} 個交易日收盤穿越均線 {CHOP_CROSSES} 次以上算「🌊 盤整」，越少代表趨勢越明確",
        "",
        "觸發價只代表「如果收盤到這個價位」，不代表一定會發生；技術分析僅供參考。",
    ]
    if waiting.empty:
        lines[1] = "目前沒有等待法則 2 的股票。"
    reb_slots = 1 if not rebound.empty else 0
    msgs, n_rest = _pack("葛蘭碧：等待法則2", [granville_bubble(r) for _, r in waiting.iterrows()],
                         MAX_MESSAGES - 1 - reb_slots)
    lines += _rest_line(waiting, n_rest, "代號", "名稱")
    lines += ["", f"【🔵 急跌反彈候選（法則 4）】共 {len(rebound)} 檔",
              f"均線往下、收盤離均線 {GRANVILLE_DEFAULTS['bias_pct']:g}% 以上（負乖離過大）。回測隔天開盤買、持有 20 個交易日，"
              "前後兩段都比全部股票平均好約 2%，但勝率只有四成多，買之前一定要設停損。"]
    if reb_slots:
        reb_msgs, reb_rest = _pack("葛蘭碧：急跌反彈候選", [rebound_bubble(r) for _, r in rebound.iterrows()], reb_slots)
        msgs += reb_msgs
        lines += _rest_line(rebound, reb_rest, "代號", "名稱")
    else:
        lines.append("今天沒有出現法則 4 的股票。")
    return [{"type": "text", "text": "\n".join(lines)[:TEXT_MAX]}] + msgs


# ===== 明日買進 =====
def picks_bubble(r):
    rows = [
        _row("排名", f"第 {int(r['rank'])} 名"),
        _row("總分", _f(r["total_score"], decimals=0)),
        _row("收盤", _f(r["close_price"])),
        _row("5日%", _f(r["change_5d"], "%"), _sign_color(r["change_5d"])),
        _row("20日%", _f(r["change_20d"], "%"), _sign_color(r["change_20d"])),
    ]
    note = "　".join(r["tags"]) if r["tags"] else ""
    reasons = r.get("reasons") or []
    if reasons:
        note = (note + "\n" if note else "") + "加分：" + "、".join(reasons[:3])
    return _bubble(r["stock_id"], r.get("stock_name") or "", r.get("industry"), f"🛒 評分第 {int(r['rank'])} 名",
                   rows, note=note or None)


def picks_messages(date, top, regime):
    """明日買進回覆：第一則文字（大盤狀態、規則、檔名），之後一則輪播（每檔一張卡）"""
    from utils.analysis import MARKET_MA
    from utils.picks import HOLD, TOP_N
    if top.empty:
        return [{"type": "text", "text": "還沒有評分資料。"}]
    ok = bool(regime) and not regime["below"]
    lines = [f"🛒 明日買進（{date} 收盤後）"]
    if regime:
        lines.append(("🟢 大盤在季線上：可以照規則買進" if ok else "🔴 大盤在季線下：先不買新股票")
                     + f"（加權指數 {regime['close']:,.0f}，{MARKET_MA} 日均線 {regime['ma']:,.0f}，{regime['gap']:+.1f}%）")
    lines += [
        "",
        f"規則：大盤在季線上時，隔天開盤買評分前 {TOP_N} 名，持有 {HOLD} 個交易日後開盤賣出。同網頁選股頁「明日買進」。",
        "回測（2025-01～2026-10）總報酬 +476%、最大回撤 −15.3%，同期大盤 +105%；期間是大多頭，以後可能明顯較低。",
        "",
    ]
    lines += [f"{int(r['rank'])}. {r['stock_id']} {r.get('stock_name') or ''}（{_f(r['total_score'], decimals=0)} 分）"
              + (f"　{'　'.join(r['tags'])}" if r["tags"] else "") for _, r in top.iterrows()]
    lines += ["", "標籤只是參考，不影響買賣：🚀 動能選股訊號、🎯 布林收斂、📐 近 5 日法則 2、⚠️ 漲多或葛蘭碧賣訊。"]
    msgs, _ = _pack("明日買進", [picks_bubble(r) for _, r in top.iterrows()], 1)
    return [{"type": "text", "text": "\n".join(lines)[:TEXT_MAX]}] + msgs


# ===== 動能股 =====
def momentum_bubble(r, pick):
    rows = [
        _row("收盤", _f(r["close_price"])),
        _row("RS", _f(r["rs_rating"], decimals=0)),
        _row("量比", _f(r["vol_ratio"])),
        _row("離52週高%", _f(r["off_high"], "%", 1)),
        _row("離50日線%", _f(r["off_ma50"], "%", 1)),
        _row("趨勢模板", r["tt"] or "—"),
        _row("新高突破", r["bo"] or "—"),
        _row("停損價", _f(r["stop"])),
        _row("每張風險", _f(r["risk"], " 元", 0)),
    ]
    return _bubble(r["stock_id"], r.get("stock_name") or "", r.get("industry"), f"🚀 {pick}", rows)


def momentum_messages(mom):
    """動能股回覆：第一則文字（三種的檔數與欄位解釋），之後每一種一則輪播（依 RS、量比排序，放不下的列代號）"""
    from utils.granville import STOP_RULE
    from utils.momentum import BREAKOUT_VOL, HOT_PCT, RS_MIN, hot_count, momentum_choices, momentum_table
    if mom.empty:
        return None, [{"type": "text", "text": "還沒有動能選股資料。"}]
    date = mom["date"].max()
    liq = mom[mom["liquid"]]
    choices = momentum_choices(liq)
    lines = [f"🚀 動能股（{date} 收盤後）",
             f"只判斷 20 日均量 500 張以上的股票，共 {len(liq):,} 檔；已剔除離 50 日線 {HOT_PCT}% 以上的漲多股 "
             f"{hot_count(liq)} 檔。同網頁選股頁「評分排行」的「動能選股」。", ""]
    lines += [f"・{pick}：{int(choices[pick].sum())} 檔" for pick in MOMENTUM_PICKS]
    lines += [
        "",
        "【分類說明】",
        "・兩者同時：同時符合趨勢模板和 52 週新高＋量增",
        "・趨勢中回檔再起：符合趨勢模板，10 日內碰過布林下軌、今天站上中軌；回測 60 日最好，但次數少",
        f"・52 週新高＋量增：收盤創 52 週新高，成交量至少是前 50 日均量的 {BREAKOUT_VOL:g} 倍",
        f"・今天剛符合趨勢模板：今天才符合趨勢模板 8 個條件（含 RS ≥ {RS_MIN}）",
        "",
        "【欄位說明】",
        "・RS：相對強度 0～99，贏過多少 % 的股票",
        "・量比：今天成交量 ÷ 前 50 日均量",
        "・離52週高%：0 代表今天就是 52 週新高",
        f"・離50日線%：收盤離 50 日均線多少 %，{HOT_PCT}% 以上算漲多，不列入",
        f"・停損價：{STOP_RULE}",
        "・每張風險：（收盤 − 停損價）× 1000 股，單位：元",
    ]
    msgs = []
    for pick in MOMENTUM_PICKS:
        view = liq[choices[pick]]
        if view.empty:
            continue
        view = momentum_table(view)
        bubbles = [momentum_bubble(r, pick) for _, r in view.iterrows()]
        # 每一種只放一則輪播，保留位置給其他種類
        packed, n_rest = _pack(f"動能股：{pick}", bubbles, 1)
        msgs += packed
        if n_rest:
            lines += ["", f"【{pick}】還有 {n_rest} 檔放不下，請直接輸入代號查："
                      + "、".join(f"{r['stock_id']} {r.get('stock_name') or ''}" for _, r in view.tail(n_rest).iterrows())]
    return date, [{"type": "text", "text": "\n".join(lines)[:TEXT_MAX]}] + msgs[:MAX_MESSAGES - 1]


def load_momentum():
    """同每日選股頁的 load_momentum：最新一天的動能選股，數字欄位轉成數值、加上名稱與產業"""
    from utils.db import get_momentum_latest, get_stock_list
    df = get_momentum_latest()
    if df.empty:
        return df
    for c in ["close_price", "high_52w", "rs_rating", "vol_ratio", "atr14"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    info = get_stock_list(include_delisted=True)[["stock_id", "stock_name", "industry", "market"]]
    return df.merge(info, on="stock_id", how="left")


def build_lists():
    """回傳 {檔名: 內容}，給 line_cards.upload 一起上傳"""
    from utils.analysis import granville_default, load_market_regime, market_warning
    from utils.picks import buy_list
    g_date, table, rebound = granville_default()
    m_date, m_msgs = momentum_messages(load_momentum())
    p_date, top = buy_list()
    regime = load_market_regime()
    warn = market_warning(regime)
    lists = {
        "granville.json": {"date": g_date, "messages": granville_messages(g_date, table, rebound)},
        "momentum.json": {"date": m_date, "messages": m_msgs},
    }
    for content in lists.values():
        add_warning(content["messages"], warn)
    # 明日買進的第一則已寫大盤狀態，不再加提醒
    lists["picks.json"] = {"date": p_date, "messages": picks_messages(p_date, top, regime)}
    return lists


def add_warning(messages, warn):
    """大盤在季線下時，把提醒加在第一則文字訊息最前面（同網頁選股頁各分頁頂端的提醒）"""
    if warn and messages and messages[0].get("type") == "text":
        messages[0]["text"] = (warn + "\n\n" + messages[0]["text"])[:TEXT_MAX]


if __name__ == "__main__":
    for name, content in build_lists().items():
        print(name, content["date"], f"{len(json.dumps(content, ensure_ascii=False).encode()):,} bytes")
        print(content["messages"][0]["text"])
