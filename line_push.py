"""
LINE 自選股推播 - 把分組的自選股推到 LINE，每檔一張完整的「個股分析卡」
（已不在每晚排程自動執行，改成在 LINE 打「自選」隨時查；需要時用手動 workflow「LINE 自選股推播（手動）」推）

內容和網頁「個股分析卡」頁是同一套程式算出來的：
    卡片數據（52 週區間、基本面、營收、籌碼、資金、技術面燈號）：utils.card
    均線買賣點：utils.analysis.granville_status
    評分排行：daily_signals
    動能條件：utils.momentum.momentum_summary
網頁的分析改了，推播也會跟著改。圖表（股價走勢、法人買賣超等）沒有放進推播。

需要的設定（GitHub → Settings → Secrets and variables → Actions）：
    LINE_CHANNEL_ACCESS_TOKEN：LINE 官方帳號的 Channel access token
    LINE_USER_ID：要推給誰（LINE Developers 後台 Basic settings 的 Your user ID）
沒設定就跳過，不算失敗。
    LINE_WATCH_OWNER（選填）：網頁登入帳號，只推這個帳號的分組；沒設定就推所有帳號的分組。

同一個評分日只推一次：推過的日期記在 Storage（archive 桶的 _state/line_push_last.txt）。
手動重推：設環境變數 LINE_FORCE=1（手動 workflow 預設會勾）。
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
BUBBLES_PER_CAROUSEL = 12      # LINE 一則輪播最多 12 張卡片
CAROUSEL_MAX_BYTES = 45_000    # 一則輪播的 JSON 上限約 50 KB，留一點餘裕
MESSAGES_PER_PUSH = 5          # 一次推播最多 5 則訊息（算一則額度）
UP, DOWN, GRAY, WARN, GOLD = "#dc2626", "#16a34a", "#6b7280", "#b45309", "#b45309"
SCORE_RECENT, SCORE_TOP = 20, 20  # 同個股分析卡：近 20 個評分日平均、進前 20 名天數


# ===== 讀取自選股與分析 =====
def load_groups(sb, owner=None):
    """
    分組與裡面的股票（依分組建立順序、股票加入順序）；空的分組不推。
    owner 是網頁登入帳號：有給就只推那個帳號的分組，沒給就推所有帳號的分組。
    """
    q = sb.table("watch_groups").select("id, name")
    if owner:
        q = q.eq("owner", owner)
    groups = q.order("id").execute().data or []
    out = []
    for g in groups:
        rows = sb.table("watchlist").select("stock_id, added_at").eq("group_id", g["id"]) \
            .order("added_at", desc=False).execute().data or []
        ids = [r["stock_id"] for r in rows]
        if ids:
            out.append((g["name"], ids))
    return out


def analyze_stock(stock_id, momentum_row=None):
    """
    一檔股票的分析卡內容，和個股分析卡頁相同的資料與計算。
    回傳 dict：info、m（card_metrics）、gran（葛蘭碧一列，算不出來為 None）、hist（評分紀錄）、mom（動能摘要）
    """
    from utils.analysis import granville_status
    from utils.card import card_metrics, load_card_data
    from utils.db import get_signal_history
    from utils.momentum import momentum_summary
    info, prices, quarterly, revenue, ins, margin, holder, utilization, _ = load_card_data(stock_id)
    out = {"info": info or {}, "m": None, "gran": None, "hist": pd.DataFrame(), "mom": None}
    if prices.empty:
        return out
    out["m"] = card_metrics(prices, quarterly, revenue, ins, margin, holder, utilization)
    cols = [c for c in ["stock_id", "date", "high_price", "low_price", "close_price", "volume"] if c in prices.columns]
    p = prices[cols].assign(stock_id=stock_id)
    i = ins[["date", "foreign_net_shares"]].assign(stock_id=stock_id) \
        if not ins.empty and "foreign_net_shares" in ins.columns else pd.DataFrame()
    g = granville_status([stock_id], p, i)
    out["gran"] = g.iloc[0].to_dict() if not g.empty else None
    out["hist"] = get_signal_history(stock_id, limit=SCORE_RECENT + 1)
    out["mom"] = momentum_summary(momentum_row) if momentum_row is not None else None
    return out


def load_momentum(ids):
    """動能資料（momentum_signals 只保留最近幾天），每檔取最新一列"""
    from datetime import date, timedelta
    from utils.db import get_rows_for_stocks
    df = get_rows_for_stocks("momentum_signals", ids, since=str(date.today() - timedelta(days=20)))
    if df.empty:
        return {}
    return {r["stock_id"]: r for r in df.sort_values("date").groupby("stock_id").tail(1).to_dict("records")}


# ===== 組 LINE 訊息 =====
def _num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(v) else v


def _f(v, suffix="", decimals=2):
    v = _num(v)
    return "N/A" if v is None else f"{v:,.{decimals}f}{suffix}"


def _sign_color(v):
    v = _num(v)
    return None if v is None or v == 0 else UP if v > 0 else DOWN


def _text(text, size="sm", color=None, bold=False, **kw):
    t = {"type": "text", "text": str(text) if str(text) else "—", "size": size, "wrap": True, **kw}
    if color:
        t["color"] = color
    if bold:
        t["weight"] = "bold"
    return t


def _row(label, value, color=None):
    """左邊標籤、右邊數值的一列"""
    return {"type": "box", "layout": "horizontal", "contents": [
        _text(label, color=GRAY, flex=5),
        _text(value, color=color, flex=6, align="end", bold=True),
    ]}


def zone_color(status):
    """RSI、KD 五級的顏色：強勢紅、超買或超賣綠、偏強偏弱金（同網頁分析卡）"""
    from utils.indicators import LAMP
    return {"bull": UP, "bear": DOWN}.get(LAMP.get(status), GOLD)


def _cross_rows(c):
    """KD、MACD 快慢線位置的一列（往上紅、往下綠）；沒有資料回傳空 list"""
    if not c:
        return []
    return [_row("　└ 方向", f"{c[0]} {c[1]}", UP if c[0] == "↑" else DOWN)]


def _trend_rows(t, key):
    """技術指標和 5 個交易日前比的一列；沒有資料回傳空 list"""
    if not t:
        return []
    from utils.card import trend_text
    return [_row("　└ 走向", trend_text(t, key), {"↑": UP, "↓": DOWN}.get(t[0], GRAY))]


LAMP_COLOR = {"bull": UP, "bear": DOWN, "neu": GOLD}


def _lv(lv):
    """回測燈號的名稱，例如「（高成長）」；算不出來不顯示"""
    return f"（{lv[0]}）" if lv and lv[0] != "N/A" else ""


def _summary_rows(s):
    """區塊總結一列：整體偏多／偏弱／多空交錯（幾紅幾綠幾金）；沒有燈號不顯示"""
    if not s or not s.get("total"):
        return []
    return [_row("總結", f"{s['verdict']}（{s['red']} 紅 {s['green']} 綠 {s['gold']} 金）", LAMP_COLOR[s["lamp"]])]


def _chip_rows(name, c, lamp):
    """法人近 10 日買賣超（張）與趨勢（由賣轉買、買超加速…）兩列；趨勢顏色是回測燈號"""
    if not c:
        return [_row(f"{name}買賣超", "N/A")]
    streak = f"・{c['streak']}" if c["streak"] else ""
    return [_row(f"{name}（10 日）", f"{c['sum10']:+,.0f} 張", _sign_color(c["sum10"])),
            _row("　└ 趨勢", f"{c['arrow']} {c['label']}{streak}", LAMP_COLOR[lamp[0]])]


def _balance_rows(tr):
    """融資或融券餘額近 5 日、20 日增減一列"""
    if not tr or tr["chg5"] is None:
        return []
    text = f"5日 {tr['chg5']:+,.0f}" + (f"／20日 {tr['chg20']:+,.0f}" if tr["chg20"] is not None else "") + " 張"
    return [_row("　└ 增減", text, GRAY)]  # 融資融券增減回測方向不一致，不上紅綠色


def _section(title, rows):
    return [{"type": "separator", "margin": "lg"}, _text(title, size="sm", bold=True, margin="md")] + rows


def _list(v):
    return [str(x) for x in v] if isinstance(v, list) else []


def stock_bubble(sid, a):
    """一檔股票的分析卡（對應個股分析卡頁的各個區塊）"""
    info, m, g, hist, mom = a["info"], a["m"], a["gran"], a["hist"], a["mom"]
    name = info.get("stock_name") or ""
    tag = "：".join(x for x in (info.get("market"), sid) if x)
    head = [_text(tag, size="xs", color=GOLD, bold=True),
            _text(name, size="xl", bold=True),
            _text(info.get("industry") or "", size="xs", color=GRAY)]
    body = []
    if m is None:
        body.append(_text("無股價資料", color=GRAY))
        return {"type": "bubble", "size": "mega",
                "header": {"type": "box", "layout": "vertical", "contents": head},
                "body": {"type": "box", "layout": "vertical", "contents": body}}

    change = _num(m["change"]) or 0
    color = UP if change > 0 else DOWN if change < 0 else GRAY
    head.append({"type": "box", "layout": "baseline", "margin": "md", "contents": [
        _text(_f(m["close"]), size="xxl", color=color, bold=True, flex=0, wrap=False),
        _text(f"{'▲' if change >= 0 else '▼'} {_f(change)} ({_f(m['change_pct'], '%')})",
              color=color, bold=True, flex=0, margin="md", wrap=False),
    ]})
    head.append(_text(f"{m['latest']['date']} 收盤", size="xs", color=GRAY))

    # ① 52 週股價區間
    body += _section("① 52 週股價區間", [
        _row("52W 低／高", f"{_f(m['low_52w'])}／{_f(m['high_52w'])}"),
        _row("位於年區間", _f(m["position_pct"], "%", 1)),
    ])
    # ② 基本面
    q = m["latest_q"]
    eps = _num(q.get("eps")) if len(q) else None
    bl = m["basic_lamps"]
    body += _section(f"② 基本面（{m['q_label'] or '最新季'}）", [
        *_summary_rows(m.get("basic_summary")),
        _row("EPS（年初累計）", _f(eps, " 元"), UP if (eps or 0) > 0 else DOWN if eps is not None else None),
        _row("　└ 年增率", _f(m["eps_yoy"], "%", 1) + _lv(bl["eps_yoy"]), LAMP_COLOR[bl["eps_yoy"][1]]),
        _row(f"本益比（{m['pe_note'] or '—'}）", (_f(m["pe_ratio"], " 倍") if m["pe_ratio"] else "N/A") + _lv(bl["pe"]),
             LAMP_COLOR[bl["pe"][1]]),
        _row("ROE（年初累計）", _f(q.get("roe") if len(q) else None, "%") + _lv(bl["roe"]), LAMP_COLOR[bl["roe"][1]]),
        _row("P/B（股價淨值比）", _f(m["pb_now"], " 倍") + _lv(bl["pb"]), LAMP_COLOR[bl["pb"][1]]),
        _row("每股淨值", _f(q.get("book_value") if len(q) else None, " 元")),
        _row("營業現金流", _f(m["op_cf"], " 億", 1), _sign_color(m["op_cf"])),
        _row("投資現金流", _f(m["inv_cf"], " 億", 1)),
        _row("自由現金流", _f(m["free_cf"], " 億", 1), _sign_color(m["free_cf"])),
    ])
    # ③ 營收
    r = m["latest_rev"]
    rv, rl = m["rev_tr"], m["rev_lamps"]
    rev = _num(r.get("revenue")) if len(r) else None
    body += _section(f"③ 營收表現（{r.get('year_month', '最新月') if len(r) else '最新月'}）", [
        *_summary_rows(m.get("rev_summary")),
        _row("月營收", _f(rev / 1000 if rev else None, " 百萬", 1)),
        _row("年增率", _f(r.get("yoy_growth") if len(r) else None, "%") + _lv(rl["yoy"]), _sign_color(r.get("yoy_growth") if len(r) else None)),
        _row("月增率", _f(r.get("mom_growth") if len(r) else None, "%"), _sign_color(r.get("mom_growth") if len(r) else None)),
        _row("累計年增率", _f(rv["cum_yoy"], "%", 1) + _lv(rl["cum"]), _sign_color(rv["cum_yoy"])),
        _row("近 3 月年增率", _f(rv["yoy3"], "%", 1) +
             (f"（前 3 月 {rv['yoy3_prev']:+.1f}%）" if rv["yoy3"] is not None and rv["yoy3_prev"] is not None else ""),
             _sign_color(rv["yoy3"])),
        _row("在近 12 月", ("新高" if rv["rank12"] == 1 else f"第 {rv['rank12']} 高") if rv["rank12"] else "N/A"),
    ] + ([_row("年增", rv["yoy_streak"])] if rv["yoy_streak"] else []))
    # ④ 籌碼面（分析卡顯示股數，這裡換算成張比較好讀）
    h = m["latest_holder"]
    hold_chg = m["hold_chg_20"]
    body += _section("④ 籌碼面（近 10 日累計＋買賣趨勢）", [
        *_summary_rows(m.get("chip_summary")),
        *[r for name in ("外資", "投信", "自營商") for r in _chip_rows(name, m["chips"].get(name), m["chip_lamps"][name])],
        _row("大戶持股比例", _f(h.get("big_holder_ratio") if len(h) else None, "%") +
             (f"（{m['big_weeks']}週 {m['big_chg']:+.2f}）" if m["big_chg"] is not None else ""), LAMP_COLOR[m["big_lamp"][0]]),
        _row("外資持股比率", _f(m["hold_latest"], "%") +
             (f"（20日 {hold_chg:+.2f}）" if hold_chg is not None else ""), _sign_color(hold_chg)),
    ])
    # ⑤ 資金面
    mg, u = m["latest_margin"], m["latest_util"]
    body += _section("⑤ 資金面（融資融券）", [
        *_summary_rows(m.get("fund_summary")),
        _row("融資餘額", _f(mg.get("margin_balance") if len(mg) else None, " 張", 0)),
        *_balance_rows(m["margin_tr"]),
        _row("融資使用率", _f(u.get("margin_rate") if len(u) else None, "%")),
        _row("融券餘額", _f(mg.get("short_balance") if len(mg) else None, " 張", 0)),
        *_balance_rows(m["short_tr"]),
        _row("融券使用率", _f(u.get("short_rate") if len(u) else None, "%")),
        _row("券資比", _f(m["short_margin_ratio"], "%", 1) + _lv(m["short_level"]), LAMP_COLOR[m["short_level"][1]]),
    ] + ([_text(f"💡 近 20 日{m['margin_hint']}", size="xs", color=LAMP_COLOR.get(m.get("margin_hint_lamp"), GRAY), margin="sm")]
         if m["margin_hint"] else []))
    # ⑥ 技術面燈號（顏色同分析卡：偏多紅、偏空或過熱綠、中性金）
    pos = _num(m["position_pct"]) or 0
    pos_name, pos_cls, _ = m["position_level"]
    lamp = {"bull": UP, "bear": DOWN, "neu": GOLD}
    tech = [
        _row("RSI", f"{_f(m['rsi_val'])}（{m['rsi_status']}）",
             zone_color(m["rsi_status"])),
        _row("KD", f"K {_f(m['kd_k'], '', 0)}／D {_f(m['kd_d'], '', 0)}（{m['kd_status']}）",
             zone_color(m["kd_status"])),
        _row("MACD 柱狀", f"{_f(m['macd_hist'])}（{'轉強' if (_num(m['macd_hist']) or 0) > 0 else '轉弱'}）",
             UP if (_num(m["macd_hist"]) or 0) > 0 else DOWN),
        _row("股價位階", f"{pos:.0f}%（{pos_name}）", lamp.get(pos_cls)),
    ]
    tech += [_row(label, f"{value}（{note if name == value else name}）", lamp.get(cls))
             for label, value, cls, name, note in m["extra_tech"]]
    # 每個指標下面一列近 5 日走向（往上紅、往下綠、持平灰）
    trends = m.get("trends") or {}
    keys = ["RSI", "KD", "MACD", "股價位階"] + [label for label, *_ in m["extra_tech"]]
    zero = m.get("macd_zero")
    zero_rows = {"MACD": [_row("　└ 零軸", f"DIF {zero[0]}", lamp.get(zero[1]))]} if zero else {}
    crosses = m.get("crosses") or {}
    tech = [r for row, key in zip(tech, keys)
            for r in [row] + zero_rows.get(key, []) + _cross_rows(crosses.get(key)) + _trend_rows(trends.get(key), key)]
    ts = m.get("tech_summary")
    if ts:
        tech = [_row("總結", f"{ts['verdict']}（{ts['red']} 紅 {ts['green']} 綠 {ts['gold']} 金）",
                     lamp.get(ts["lamp"]))] + tech
    body += _section("⑥ 技術面燈號", tech)

    # 📐 均線買賣點
    rows = []
    if g is None:
        rows.append(_text("股價資料不足，暫時無法判斷葛蘭碧法則", color=GRAY))
    else:
        rows.append(_text(g["狀態"], bold=True))
        rows.append(_row("20 日均線", f"{_f(g['均線'])}（{g['均線方向']}）"))
        rows.append(_row("乖離", _f(g["乖離%"], "%"), _sign_color(g["乖離%"])))
        rows.append(_text(f"💬 {g['說明']}", color=GRAY))
        if _num(g.get("買進觸發價")) is not None:
            rows.append(_row("買進觸發價", f"收盤 ≥ {_f(g['買進觸發價'])}"))
        if _num(g.get("停損價")) is not None:
            risk = _num(g.get("每張風險"))
            rows.append(_row("停損價", _f(g["停損價"]) + (f"（每張約虧 {risk:,.0f}）" if risk is not None else "")))
        if _num(g.get("賣出觸發價")) is not None:
            rows.append(_row("賣出觸發價", f"收盤 ≤ {_f(g['賣出觸發價'])}（{_f(g['賣出距離%'], '%')}）"))
        rows.append(_row("趨勢", g["趨勢"]))
    body += _section("📐 均線買賣點", rows)

    # 🏆 評分排行
    rows = []
    if hist.empty:
        rows.append(_text("沒有評分（可能是新上市或資料不足）", color=GRAY))
    else:
        now = hist.iloc[-1]
        prev = _num(hist.iloc[-2]["total_score"]) if len(hist) > 1 else None
        total = _num(now["total_score"]) or 0
        diff = total - prev if prev is not None else None
        recent = hist.tail(SCORE_RECENT)
        ranks = pd.to_numeric(recent["rank"], errors="coerce")
        rank = _num(now.get("rank"))
        rows.append(_row("總分", f"{total:.0f} / 100" + (f"（{diff:+.0f}）" if diff is not None else ""), _sign_color(diff)))
        rows.append(_row("排名", f"第 {rank:.0f} 名" if rank is not None else "未列入排名"))
        rows.append(_row(f"近 {SCORE_RECENT} 日", f"平均 {pd.to_numeric(recent['total_score'], errors='coerce').mean():.1f} 分、"
                                                   f"進前 {SCORE_TOP} 名 {int((ranks <= SCORE_TOP).sum())} 天"))
        rows.append(_text(f"籌碼 {_f(now.get('chip_score'), '', 0)}/32、資金 {_f(now.get('margin_score'), '', 0)}/8、"
                          f"基本面 {_f(now.get('fund_score'), '', 0)}/35、技術 {_f(now.get('tech_score'), '', 0)}/25"))
        reasons, warnings = _list(now.get("reasons")), _list(now.get("warnings"))
        rows.append(_text("✅ 加分理由：" + ("、".join(reasons) or "無")))
        if warnings:
            rows.append(_text("⚠️ 風險提醒：" + "、".join(warnings), color=WARN))
    body += _section("🏆 評分排行", rows)

    # 🚀 動能條件
    if mom is None:
        rows = [_text("還沒有這檔股票的動能資料", color=GRAY)]
    else:
        rows = [
            _row("趨勢模板", f"{mom['tt_text']}（{mom['tt_note']}）"),
            _row("52 週新高突破", f"{mom['bo_text']}（{mom['bo_note']}）"),
            _row("相對強度 RS", _f(mom["rs"], "", 0) if _num(mom["rs"]) is not None else "—"),
        ]
        if mom["stop"]:
            rows.append(_row("停損價（以收盤買進）", _f(mom["stop"])))
    body += _section("🚀 動能條件", rows)

    return {"type": "bubble", "size": "mega",
            "header": {"type": "box", "layout": "vertical", "contents": head},
            "body": {"type": "box", "layout": "vertical", "spacing": "xs", "contents": body}}


def _carousels(bubbles):
    """把卡片分成多則輪播：每則最多 12 張，JSON 不超過上限"""
    chunks, cur = [], []
    for b in bubbles:
        trial = cur + [b]
        if cur and (len(trial) > BUBBLES_PER_CAROUSEL or
                    len(json.dumps({"type": "carousel", "contents": trial}, ensure_ascii=False).encode()) > CAROUSEL_MAX_BYTES):
            chunks.append(cur)
            trial = [b]
        cur = trial
    if cur:
        chunks.append(cur)
    return chunks


def build_messages(groups, sig_date, analyses):
    """第一則是文字摘要，之後每個分組的分析卡輪播（太多就分成多則）"""
    lines = [f"⭐ 自選股分析卡（評分日 {sig_date}）"]
    for name, ids in groups:
        counts = {}
        for sid in ids:
            g = analyses[sid]["gran"]
            if g and not str(g["狀態"]).startswith("—"):
                counts[g["狀態"]] = counts.get(g["狀態"], 0) + 1
        lines.append(f"【{name}】{len(ids)} 檔")
        lines += [f"　{state}：{n} 檔" for state, n in counts.items()]
    messages = [{"type": "text", "text": "\n".join(lines)[:5000]}]

    for name, ids in groups:
        bubbles = [stock_bubble(sid, analyses[sid]) for sid in ids]
        for chunk in _carousels(bubbles):
            labels = [b["header"]["contents"][1]["text"] for b in chunk]
            messages.append({"type": "flex", "altText": f"{name}：{'、'.join(labels)}"[:400],
                             "contents": {"type": "carousel", "contents": chunk}})
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


def run(sb, token, user_id, force=False, post=requests.post, dry_run=False, owner=None):
    """回傳推了幾檔（沒推回傳 0）"""
    groups = load_groups(sb, owner)
    if not groups:
        print(f"::warning title=沒有推播::帳號 {owner} 底下沒有任何自選股，請確認 LINE_WATCH_OWNER 是網頁的登入帳號"
              if owner else "沒有任何自選股，不推播")
        return 0
    ids = list(dict.fromkeys(sid for _, g in groups for sid in g))
    from utils.db import get_signal_dates
    sig_dates = get_signal_dates(1)
    if not sig_dates:
        print("還沒有評分資料，不推播")
        return 0
    sig_date = sig_dates[0]
    if not force and last_pushed(sb) == sig_date:
        print(f"{sig_date} 已經推播過，不重複推")
        return 0
    momentum = load_momentum(ids)
    analyses = {sid: analyze_stock(sid, momentum.get(sid)) for sid in ids}
    messages = build_messages(groups, sig_date, analyses)
    if dry_run:
        print(messages[0]["text"])
        for m in messages[1:]:
            print(f"卡片輪播 {len(m['contents']['contents'])} 張，{len(json.dumps(m, ensure_ascii=False).encode()):,} bytes")
            for b in m["contents"]["contents"]:
                texts = [c["text"] for c in b["header"]["contents"] if c.get("type") == "text"]
                for c in b["body"]["contents"]:
                    if c.get("type") == "text":
                        texts.append(c["text"])
                    elif c.get("type") == "box":
                        texts.append("：".join(x["text"] for x in c["contents"] if x.get("type") == "text"))
                print("　" + "｜".join(texts))
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
    run(supabase, token, user_id, force=os.getenv("LINE_FORCE") == "1", dry_run=dry_run,
        owner=(os.getenv("LINE_WATCH_OWNER") or "").strip() or None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
