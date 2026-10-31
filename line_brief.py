"""
LINE 打「早報」的回覆 - 每日台股早報的摘要卡

早報由 Claude 每天早上產生（data.json 格式同 briefs/<日期>/data.json），
再用 GitHub Actions「LINE 早報卡片（手動）」把 data.json 傳進來，這裡做成一張 Flex 卡片存到 Storage：
    archive 桶 / _line_cards / brief.json，內容是 {"date": 早報日期, "messages": [LINE 訊息]}
LINE 機器人（supabase/functions/line-webhook）收到「早報」時原樣回覆；是回覆訊息，不算推播額度。

用法：python line_brief.py <data.json> [完整早報網址]
需要 SUPABASE_URL、SUPABASE_KEY；設 LINE_BRIEF_DRY_RUN=1 只印出卡片不上傳。
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app"))

from line_push import GRAY, _row, _sign_color, _text  # noqa: E402

FILE = "brief.json"
US_KEYS = ["那斯達克", "費城半導體"]          # 美股只列這兩個指數，其他看完整早報
TW_KEYS = ["加權指數", "櫃買指數", "外資買賣超", "台指期夜盤"]
SIGNAL_MAX = 60                               # 每檔訊號只留第一句，避免卡片太長


def _pct(v):
    return f"{v:+.2f}%" if v is not None else "N/A"


def _sep():
    return {"type": "separator", "margin": "md"}


def _title(text):
    return _text(text, size="sm", color=GRAY, bold=True, margin="md")


def _first_sentence(text):
    s = text.split("。")[0]
    return s if len(s) <= SIGNAL_MAX else s[:SIGNAL_MAX - 1] + "…"


def _quote_rows(items, keys):
    rows = []
    for k in keys:
        q = next((x for x in items if x["name"].startswith(k)), None)
        if q:
            rows.append(_row(q["name"], f'{q["close"]:,.2f}　{_pct(q["chg_pct"])}', _sign_color(q["chg_pct"])))
    return rows


def _tw_rows(rows):
    out = []
    for k in TW_KEYS:
        r = next((x for x in rows if x["name"].startswith(k)), None)
        if r:
            label = r["name"].split("（")[0]
            out.append(_row(label, r["value"], _sign_color(r.get("dir"))))
    return out


def _stock_box(s):
    return {"type": "box", "layout": "vertical", "margin": "md", "contents": [
        {"type": "box", "layout": "horizontal", "contents": [
            _text(f'{s["name"]} {s["id"]}', bold=True, flex=6),
            _text(f'{s["close"]:,.0f}　{_pct(s["chg_pct"])}', color=_sign_color(s["chg_pct"]), bold=True, flex=6, align="end"),
        ]},
        _text(_first_sentence(s["signal"]), size="xs", color=GRAY),
    ]}


def brief_bubble(d, url=None):
    tsm = next((a for a in d["adr"] if "TSM" in a["name"]), None)
    us = _quote_rows(d["us_index"], US_KEYS)
    if tsm:
        us.append(_row("台積電 ADR", f'{tsm["close"]:,.2f}　{_pct(tsm["chg_pct"])}', _sign_color(tsm["chg_pct"])))
        if tsm.get("sub"):
            us.append(_text(tsm["sub"], size="xs", color=GRAY, align="end"))
    body = [_text(d["headline"], size="sm"), _sep(),
            _title(f'美股（{d["us_date"]}）'), *us, _sep(),
            _title(f'台股（{d["tw_prev_date"]}）'), *_tw_rows(d["tw_market"]), _sep(),
            _title("關注股"), *[_stock_box(s) for s in d["stocks"]], _sep(),
            _title("今天怎麼看（推論）"), _text(d["views"][-1], size="xs")]
    bubble = {"type": "bubble", "size": "mega",
              "header": {"type": "box", "layout": "vertical", "contents": [_text(d["title"], size="lg", bold=True)]},
              "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body}}
    if url:
        bubble["footer"] = {"type": "box", "layout": "vertical", "contents": [
            {"type": "button", "style": "primary", "height": "sm",
             "action": {"type": "uri", "label": "看完整早報", "uri": url}}]}
    return bubble


def brief_messages(d, url=None):
    return {"date": d["date_tw"],
            "messages": [{"type": "flex", "altText": f'{d["title"]}：{d["headline"][:60]}', "contents": brief_bubble(d, url)}]}


def main():
    d = json.loads(open(sys.argv[1], encoding="utf-8").read())
    url = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None
    content = brief_messages(d, url)
    if os.getenv("LINE_BRIEF_DRY_RUN") == "1":
        print(json.dumps(content, ensure_ascii=False, indent=1))
        return
    from line_cards import BUCKET, PREFIX
    from utils.db import supabase
    data = json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode()
    supabase.storage.from_(BUCKET).upload(f"{PREFIX}/{FILE}", data, {"content-type": "application/json", "upsert": "true"})
    print(f"已上傳 {PREFIX}/{FILE}（{d['date_tw']}，{len(data):,} bytes）")


if __name__ == "__main__":
    main()
