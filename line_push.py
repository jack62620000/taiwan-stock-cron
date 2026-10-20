"""
LINE 每日推播 - 每晚資料更新、評分完之後，把所有分組的自選股分析推到 LINE

分析和網頁「自選股」頁是同一套程式：
    資料：utils.analysis.load_watch_inputs
    葛蘭碧狀態：utils.analysis.granville_status（明天開盤可買／等待法則2／賣出訊號、觸發價、停損價）
    每日選股評分：daily_signals（總分、排名、四項分數、加分理由、風險提醒）
網頁的分析改了，推播也會跟著改。

需要的設定（GitHub → Settings → Secrets and variables → Actions）：
    LINE_CHANNEL_ACCESS_TOKEN：LINE 官方帳號的 Channel access token
    LINE_USER_ID：要推給誰（LINE Developers 後台 Basic settings 的 Your user ID）
沒設定就跳過，不算失敗。

同一個評分日只推一次：推過的日期記在 Storage（archive 桶的 _state/line_push_last.txt），
早上補抓的排程又跑到這裡時不會重複推。手動重推：設環境變數 LINE_FORCE=1。
只想看會推出什麼、不真的推：設 LINE_DRY_RUN=1（不需要 LINE 金鑰）。
"""
import json
import os
import sys
import uuid

import pandas as pd
import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app"))

PUSH_URL = "https://api.line.me/v2/bot/message/push"
STATE_BUCKET = "archive"
STATE_PATH = "_state/line_push_last.txt"
BUBBLES_PER_CAROUSEL = 12  # LINE 一則輪播最多 12 張卡片
MESSAGES_PER_PUSH = 5      # 一次推播最多 5 則訊息（算一則額度）
UP, DOWN, GRAY, WARN = "#dc2626", "#16a34a", "#6b7280", "#b45309"


# ===== 讀取自選股與分析 =====
def load_groups(sb):
    """所有分組與裡面的股票（依分組建立順序、股票加入順序）；空的分組不推"""
    groups = sb.table("watch_groups").select("id, name").order("id").execute().data or []
    out = []
    for g in groups:
        rows = sb.table("watchlist").select("stock_id, added_at").eq("group_id", g["id"]) \
            .order("added_at", desc=False).execute().data or []
        ids = [r["stock_id"] for r in rows]
        if ids:
            out.append((g["name"], ids))
    return out


def analyze(ids, names):
    """跑和自選股頁相同的分析，回傳 (評分日, 葛蘭碧表, 最新評分表, 股價)"""
    from utils.analysis import granville_status, load_watch_inputs
    sig_dates, signals, prices, ins, _ = load_watch_inputs(ids, score_days=2)
    gran = granville_status(ids, prices, ins, names) if not prices.empty else pd.DataFrame()
    latest = pd.DataFrame()
    if not signals.empty:
        latest = signals.sort_values("date").groupby("stock_id").tail(1).set_index("stock_id")
    return (sig_dates[0] if sig_dates else None), gran, latest, prices


# ===== 組 LINE 訊息 =====
def _num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(v) else v


def _pct_text(v):
    v = _num(v)
    if v is None:
        return "—", GRAY
    return f"{'▲' if v > 0 else '▼' if v < 0 else ''}{v:+.2f}%", UP if v > 0 else DOWN if v < 0 else GRAY


def _row(label, value, color=None):
    return {"type": "box", "layout": "horizontal", "contents": [
        {"type": "text", "text": label, "size": "sm", "color": GRAY, "flex": 2},
        {"type": "text", "text": value, "size": "sm", "flex": 5, "wrap": True, "align": "end",
         **({"color": color} if color else {})},
    ]}


def _list(v):
    return [str(x) for x in v] if isinstance(v, list) else []


def stock_bubble(sid, name, gran, sig, prices):
    """一檔股票的卡片：收盤與漲跌、葛蘭碧狀態、每日選股評分"""
    p = prices[prices["stock_id"] == sid].sort_values("date") if not prices.empty else prices
    close = pd.to_numeric(p["close_price"], errors="coerce").dropna() if not p.empty else pd.Series(dtype=float)
    day_pct = (close.iloc[-1] / close.iloc[-2] - 1) * 100 if len(close) > 1 else None
    pct, color = _pct_text(day_pct)
    head = [
        {"type": "text", "text": f"{sid} {name}".strip(), "weight": "bold", "size": "lg", "wrap": True},
        {"type": "box", "layout": "baseline", "contents": [
            {"type": "text", "text": f"{close.iloc[-1]:,.2f}" if not close.empty else "—",
             "size": "xl", "weight": "bold", "color": color, "flex": 0},
            {"type": "text", "text": pct, "size": "sm", "color": color, "margin": "md", "flex": 0},
        ]},
    ]
    body = []
    if sig is not None:
        for label, col in (("5日", "change_5d"), ("20日", "change_20d")):
            t, c = _pct_text(sig.get(col))
            body.append(_row(label, t, c))

    body.append({"type": "separator", "margin": "md"})
    body.append({"type": "text", "text": "📐 葛蘭碧", "size": "xs", "color": GRAY, "margin": "md"})
    if gran is not None:
        body.append({"type": "text", "text": str(gran["狀態"]), "weight": "bold", "wrap": True})
        if gran.get("說明"):
            body.append({"type": "text", "text": str(gran["說明"]), "size": "sm", "wrap": True, "color": GRAY})
        for label, col in (("買進觸發價", "買進觸發價"), ("停損價", "停損價"), ("賣出觸發價", "賣出觸發價")):
            v = _num(gran.get(col))
            if v is not None:
                body.append(_row(label, f"{v:,.2f}"))
        if gran.get("趨勢"):
            body.append(_row("趨勢", str(gran["趨勢"])))
    else:
        body.append({"type": "text", "text": "股價資料不足，算不出葛蘭碧狀態", "size": "sm", "wrap": True, "color": GRAY})

    body.append({"type": "separator", "margin": "md"})
    body.append({"type": "text", "text": "🎯 每日選股評分", "size": "xs", "color": GRAY, "margin": "md"})
    if sig is not None and _num(sig.get("total_score")) is not None:
        rank = _num(sig.get("rank"))
        body.append({"type": "text", "weight": "bold", "wrap": True,
                     "text": f"{_num(sig['total_score']):.0f} 分" + (f"（第 {rank:.0f} 名）" if rank is not None else "")})
        parts = [f"{label} {_num(sig.get(col)):.0f}" for label, col in
                 (("籌碼", "chip_score"), ("資金", "margin_score"), ("基本", "fund_score"), ("技術", "tech_score"))
                 if _num(sig.get(col)) is not None]
        if parts:
            body.append({"type": "text", "text": "　".join(parts), "size": "sm", "wrap": True})
        reasons, warnings = _list(sig.get("reasons")), _list(sig.get("warnings"))
        if reasons:
            body.append({"type": "text", "text": "✚ " + "、".join(reasons[:4]), "size": "sm", "wrap": True})
        if warnings:
            body.append({"type": "text", "text": "⚠ " + "、".join(warnings[:4]), "size": "sm", "wrap": True, "color": WARN})
    else:
        body.append({"type": "text", "text": "沒有評分資料", "size": "sm", "color": GRAY})

    return {"type": "bubble", "size": "kilo",
            "header": {"type": "box", "layout": "vertical", "contents": head},
            "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body}}


def build_messages(groups, names, sig_date, gran, latest, prices):
    """第一則是文字摘要，之後每個分組一則卡片輪播（超過 12 檔分成多則）"""
    gran_by_id = {r["代號"]: r for r in gran.to_dict("records")} if not gran.empty else {}
    sig_by_id = latest.to_dict("index") if not latest.empty else {}

    lines = [f"⭐ 自選股分析（評分日 {sig_date}）"]
    for name, ids in groups:
        counts = {}
        for sid in ids:
            state = gran_by_id.get(sid, {}).get("狀態")
            if state and not state.startswith("—"):
                counts[state] = counts.get(state, 0) + 1
        lines.append(f"【{name}】{len(ids)} 檔")
        lines += [f"　{state}：{n} 檔" for state, n in counts.items()]
    messages = [{"type": "text", "text": "\n".join(lines)[:5000]}]

    for name, ids in groups:
        for k in range(0, len(ids), BUBBLES_PER_CAROUSEL):
            chunk = ids[k:k + BUBBLES_PER_CAROUSEL]
            bubbles = [stock_bubble(sid, names.get(sid, ""), gran_by_id.get(sid), sig_by_id.get(sid), prices)
                       for sid in chunk]
            messages.append({"type": "flex", "altText": f"{name}：{'、'.join(chunk)}"[:400],
                             "contents": {"type": "carousel", "contents": bubbles}})
    return messages


# ===== 推播 =====
def push(token, user_id, messages, post=requests.post):
    """每 5 則訊息推一次；X-Line-Retry-Key 讓重送時 LINE 不會重複推"""
    for k in range(0, len(messages), MESSAGES_PER_PUSH):
        res = post(PUSH_URL, timeout=30, json={"to": user_id, "messages": messages[k:k + MESSAGES_PER_PUSH]},
                   headers={"Authorization": f"Bearer {token}", "X-Line-Retry-Key": str(uuid.uuid4())})
        if res.status_code != 200:
            raise RuntimeError(f"LINE 推播失敗（{res.status_code}）：{res.text[:300]}")


def last_pushed(sb):
    try:
        return sb.storage.from_(STATE_BUCKET).download(STATE_PATH).decode().strip()
    except Exception:
        return None


def mark_pushed(sb, day):
    sb.storage.from_(STATE_BUCKET).upload(STATE_PATH, day.encode(), {"content-type": "text/plain", "upsert": "true"})


def run(sb, token, user_id, force=False, post=requests.post, dry_run=False):
    """回傳推了幾檔（沒推回傳 0）"""
    from utils.db import get_stock_list
    groups = load_groups(sb)
    if not groups:
        print("沒有任何自選股，不推播")
        return 0
    ids = list(dict.fromkeys(sid for _, g in groups for sid in g))
    stocks = get_stock_list(include_delisted=True)
    names = dict(zip(stocks["stock_id"], stocks["stock_name"].fillna(""))) if not stocks.empty else {}
    sig_date, gran, latest, prices = analyze(ids, names)
    if sig_date is None:
        print("還沒有評分資料，不推播")
        return 0
    if not force and last_pushed(sb) == sig_date:
        print(f"{sig_date} 已經推播過，不重複推")
        return 0
    messages = build_messages(groups, names, sig_date, gran, latest, prices)
    if dry_run:
        print(messages[0]["text"])
        for m in messages[1:]:
            print(f"卡片輪播 {len(m['contents']['contents'])} 張，{len(json.dumps(m, ensure_ascii=False).encode()):,} bytes")
            for b in m["contents"]["contents"]:
                print("　" + "｜".join(c["text"] for c in b["body"]["contents"] if c.get("type") == "text"))
        return 0
    push(token, user_id, messages, post=post)
    mark_pushed(sb, sig_date)
    n = sum(len(g) for _, g in groups)
    print(f"已推播 {sig_date}：{len(groups)} 個分組、{n} 檔，共 {len(messages)} 則訊息")
    return n


def main():
    token, user_id = os.getenv("LINE_CHANNEL_ACCESS_TOKEN"), os.getenv("LINE_USER_ID")
    dry_run = os.getenv("LINE_DRY_RUN") == "1"
    if not dry_run and (not token or not user_id):
        print("沒有設定 LINE_CHANNEL_ACCESS_TOKEN／LINE_USER_ID，跳過 LINE 推播")
        return 0
    from utils.db import supabase
    run(supabase, token, user_id, force=os.getenv("LINE_FORCE") == "1", dry_run=dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
