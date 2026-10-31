"""
早報備援 - 早上 8:30 還沒有今天的早報時，用資料庫的數字自動排一份簡易版（.github/workflows/brief_fallback.yml）

完整早報由每天早上的 Claude 排程產生（含新聞、分析、行事曆），再呼叫「LINE 早報（手動）」排成圖片。
那邊沒跑時，這裡讀 _line_cards/brief.json 的日期，不是今天就自己組一份 data.json（格式同 tests/brief_sample.json），
交給 line_brief.py 排成圖片上傳，LINE 打「早報」至少看得到美股收盤、台股大盤與法人、自選股的數字。
簡易版沒有新聞和推論；之後 Claude 的完整早報一產生就會直接覆蓋。

只在週一到週六執行（週日沒有新的盤後資料）。
用法：python brief_fallback.py（需要 SUPABASE_URL／KEY、ETF_SUPABASE_URL／KEY；LINE_WATCH_OWNER 選填，
指定用哪個網頁帳號的自選股；設 LINE_BRIEF_DRY_RUN=<資料夾> 只產生圖片不上傳；FORCE=1 不管今天有沒有早報都產生）
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from supabase import create_client

from retry import execute as _execute

load_dotenv()

WEEKDAYS = "一二三四五六日"
MAX_STOCKS = 4
US_INDEX = ["^DJI", "^GSPC", "^IXIC", "^SOX", "^VIX"]
RATES = [("UST2Y", "美債 2 年", "bp"), ("UST10Y", "美債 10 年", "bp"), ("CL=F", "WTI 原油", "pct"),
         ("BZ=F", "Brent 原油", "pct"), ("DX-Y.NYB", "美元指數", "pct"), ("TWD=X", "美元／台幣", "pct")]


def current_brief_date(url, key):
    """Storage 裡 brief.json 的日期（'YYYY-MM-DD'）；還沒有或讀不到回傳 None"""
    try:
        r = requests.get(f"{url.rstrip('/')}/storage/v1/object/archive/_line_cards/brief.json",
                         headers={"Authorization": f"Bearer {key}", "apikey": key}, timeout=60)
        r.raise_for_status()
        return r.json().get("date")
    except Exception:
        return None


def md(day):
    """'2026-10-30' → '10/30（五）'"""
    d = datetime.strptime(day, "%Y-%m-%d")
    return f"{d.month}/{d.day}（{WEEKDAYS[d.weekday()]}）"


def sign(v):
    return None if v is None else (1 if v > 0 else -1 if v < 0 else 0)


def f(v):
    return None if v is None else float(v)


# ==================== 美股 ====================
def us_sections(rows):
    """us_market_daily 同一天的列 → us_index、rates、adr、mag7、semi"""
    by = {r["symbol"]: r for r in rows}

    def quotes(symbols):
        return [{"name": by[s]["name"], "close": f(by[s]["close"]), "chg_pct": f(by[s]["change_pct"])}
                for s in symbols if s in by and by[s].get("close") is not None and by[s].get("change_pct") is not None]

    rates = []
    for sym, name, kind in RATES:
        r = by.get(sym)
        if not r:
            continue
        if kind == "bp":
            chg = f(r["change"])
            rates.append({"name": name, "value": f"{f(r['close']):.2f}%",
                          "change": "" if chg is None else f"{chg * 100:+.0f} bp".replace("-", "−"), "dir": sign(chg)})
        else:
            p = f(r["change_pct"])
            rates.append({"name": name, "value": f"{f(r['close']):,.2f}", "change": "" if p is None else f"{p:+.2f}%".replace("-", "−"),
                          "dir": sign(p)})
    cat = lambda c: sorted((r["symbol"] for r in rows if r.get("category") == c),  # noqa: E731
                           key=lambda s: -abs(f(by[s]["change_pct"]) or 0))
    return {"us_index": quotes(US_INDEX), "rates": rates,
            "adr": quotes(cat("adr")), "mag7": quotes(cat("mag7")), "semi": quotes(cat("semi"))}


# ==================== 台股大盤與法人 ====================
def tw_market(index_rows, futures_rows, ins_rows):
    """
    index_rows：market_index 最新一天（TAIEX、TPEX）；futures_rows：futures_institutional 最新一天；
    ins_rows：stock_ins_daily 最新一天所有個股。回傳 tw_market 列表
    """
    out = []
    names = {"TAIEX": "加權指數", "TPEX": "櫃買指數"}
    for code in ("TAIEX", "TPEX"):
        r = next((x for x in index_rows if x["index_code"] == code), None)
        if not r:
            continue
        close, chg = f(r["close"]), f(r["change"])
        prev = close - chg if close is not None and chg is not None else None
        change = "" if chg is None else f"{chg:+,.2f}（{chg / prev * 100:+.2f}%）" if prev else f"{chg:+,.2f}"
        out.append({"name": names[code], "value": f"{close:,.2f}", "change": change.replace("-", "−"), "dir": sign(chg)})
        if code == "TAIEX" and r.get("trade_value"):
            out.append({"name": "上市成交值", "value": f"{f(r['trade_value']) / 1e8:,.0f} 億", "change": "", "dir": None})
    for key, label in (("foreign_net_shares", "外資買賣超"), ("sitca_net_shares", "投信買賣超"), ("dealers_net_shares", "自營商買賣超")):
        vals = [x[key] for x in ins_rows if x.get(key) is not None]
        if vals:
            lots = sum(vals) / 1000
            out.append({"name": label, "value": f"{lots:+,.0f} 張".replace("-", "−"), "change": "上市櫃個股合計", "dir": sign(lots)})
    fx = next((x for x in futures_rows if x.get("item") == "外資及陸資"), None)
    if fx and fx.get("net_oi") is not None:
        out.append({"name": "外資台指期淨部位", "value": f"{fx['net_oi']:+,} 口".replace("-", "−"), "change": "",
                    "dir": sign(fx["net_oi"])})
    return out


# ==================== 自選股 ====================
def stock_card(stock_id, name, industry, prices, ins, signal):
    """
    prices：daily_prices 由舊到新（至少 2 筆）；ins：stock_ins_daily 最近 5 天；signal：daily_signals 最新一筆或 None
    回傳 brief 的 stocks 一項（欄位同 tests/brief_sample.json）
    """
    closes = [f(p["close_price"]) for p in prices if p.get("close_price") is not None]
    last = closes[-1]

    def ret(n):
        return round((last / closes[-1 - n] - 1) * 100, 2) if len(closes) > n and closes[-1 - n] else None

    def ma(n, upto=None):
        seg = closes[:upto][-n:] if upto else closes[-n:]
        return sum(seg) / n if len(seg) == n else None

    flags = [(f"{n} 日", last >= ma(n)) for n in (5, 20, 60) if ma(n) is not None]
    tail = closes[-20:]
    ma20 = [ma(20, len(closes) - len(tail) + i + 1) for i in range(len(tail))]

    def net(key):
        vals = [x[key] for x in ins if x.get(key) is not None]
        if not vals:
            return None, "—"
        lots = sum(vals) / 1000
        return sign(lots), f"{lots:+,.0f} 張".replace("-", "−")

    f5, f5_text = net("foreign_net_shares")
    s5, s5_text = net("sitca_net_shares")
    score = (f"{signal['total_score']:.0f} 分／第 {signal['rank']} 名"
             if signal and signal.get("total_score") is not None and signal.get("rank") else "—")
    return {"id": stock_id, "name": name, "tag": industry or "—", "close": last, "chg_pct": ret(1),
            "ret5": ret(5), "ret20": ret(20), "ma_flags": flags, "f5": f5, "f5_text": f5_text, "s5": s5, "s5_text": s5_text,
            "margin_text": "—", "holder_text": "—",
            "rev_text": "—", "score_text": score, "signal_level": "neutral",
            "signal": "簡易版只列數字，沒有價位分析。", "spark": {"closes": tail, "ma20": ma20, "low": None}}


def watch_stocks(main, owner):
    """網頁帳號 owner 的自選股（沒指定就全部帳號），最多 MAX_STOCKS 檔，依分組、加入順序"""
    q = lambda: main.table("watch_groups").select("id,owner").order("id")  # noqa: E731
    groups = [g["id"] for g in _execute(q).data if not owner or g["owner"] == owner]
    if not groups:
        return []
    rows = _execute(lambda: main.table("watchlist").select("stock_id,group_id,added_at")
                    .in_("group_id", groups).order("added_at")).data
    seen = []
    for r in sorted(rows, key=lambda r: groups.index(r["group_id"])):
        if r["stock_id"] not in seen:
            seen.append(r["stock_id"])
    return seen[:MAX_STOCKS]


def build_stocks(main, ids, tw_date):
    out = []
    for sid in ids:
        info = _execute(lambda: main.table("stock_info").select("stock_name,industry").eq("stock_id", sid)).data
        prices = _execute(lambda: main.table("daily_prices").select("date,close_price").eq("stock_id", sid)
                          .lte("date", tw_date).order("date", desc=True).limit(61)).data[::-1]
        if len(prices) < 2:
            continue
        ins = _execute(lambda: main.table("stock_ins_daily").select("date,foreign_net_shares,sitca_net_shares")
                       .eq("stock_id", sid).lte("date", tw_date).order("date", desc=True).limit(5)).data[::-1]
        sig = _execute(lambda: main.table("daily_signals").select("total_score,rank").eq("stock_id", sid)
                       .order("date", desc=True).limit(1)).data
        name = info[0]["stock_name"] if info else sid
        out.append(stock_card(sid, name, info[0].get("industry") if info else None, prices, ins, sig[0] if sig else None))
    return out


# ==================== 組合 ====================
def headline(us, tw):
    parts = []
    for q in us["us_index"]:
        if q["name"] in ("S&P 500", "費城半導體") and q["chg_pct"] is not None:
            parts.append(f"{q['name']} {q['chg_pct']:+.2f}%")
    tsm = next((q for q in us["adr"] if "台積電" in q["name"]), None)
    if tsm and tsm["chg_pct"] is not None:
        parts.append(f"台積電 ADR {tsm['chg_pct']:+.2f}%")
    tw_txt = next((f"加權指數收 {r['value']}，{r['change']}" for r in tw if r["name"] == "加權指數"), "")
    us_txt = "美股：" + "、".join(parts) + "。" if parts else ""
    return (f"今天的完整早報還沒產生，這是系統用資料庫數字自動整理的簡易版（沒有新聞與分析）。{us_txt}"
            + (f"台股前一交易日{tw_txt}。" if tw_txt else "")).replace("-", "−")


def build_data(main, etf, today, owner=None):
    us_date = _execute(lambda: etf.table("us_market_daily").select("date").order("date", desc=True).limit(1)).data[0]["date"]
    us_rows = _execute(lambda: etf.table("us_market_daily").select("symbol,name,category,close,change,change_pct")
                       .eq("date", us_date)).data
    tw_date = _execute(lambda: main.table("market_index").select("date").order("date", desc=True).limit(1)).data[0]["date"]
    index_rows = _execute(lambda: main.table("market_index").select("index_code,close,change,trade_value").eq("date", tw_date)).data
    futures_rows = _execute(lambda: main.table("futures_institutional").select("item,net_oi").eq("date", tw_date)).data
    ins_rows = []
    while True:  # 一天約 2,000 檔，分頁讀
        page = _execute(lambda: main.table("stock_ins_daily").select("foreign_net_shares,sitca_net_shares,dealers_net_shares")
                        .eq("date", tw_date).range(len(ins_rows), len(ins_rows) + 999)).data
        ins_rows += page
        if len(page) < 1000:
            break
    us = us_sections(us_rows)
    tw = tw_market(index_rows, futures_rows, ins_rows)
    t = datetime.strptime(today, "%Y-%m-%d")
    return {
        "title": f"台股早報 {t.month}/{t.day}（{WEEKDAYS[t.weekday()]}）簡易版",
        "date_tw": today, "us_date": us_date, "tw_prev_date": tw_date, "headline": headline(us, tw), **us,
        "news": [], "tw_market": tw, "stocks": build_stocks(main, watch_stocks(main, owner), tw_date),
        "views": ["這是系統自動產生的簡易版，只有數字，沒有新聞、推論與價位分析。",
                  "完整早報產生後會自動取代這一份；可以到網頁看各股分析卡。"],
        "calendar": [], "sources": ["證交所", "櫃買中心", "期交所", "Yahoo Finance（美股）"],
        "data_note": f"系統於 {datetime.now(timezone.utc) + timedelta(hours=8):%H:%M} 自動產生（完整早報當時尚未產生）。"
                     f"美股為 {md(us_date)} 收盤，台股為 {md(tw_date)} 盤後資料。",
    }


def main():
    today = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")
    url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"]
    have = current_brief_date(url, key)
    if have == today and os.getenv("FORCE") != "1":
        print(f"已經有 {today} 的早報，不需要備援")
        return 0
    print(f"現在的早報是 {have or '（沒有）'}，產生 {today} 簡易版")
    main_db = create_client(url, key)
    etf_db = create_client(os.environ["ETF_SUPABASE_URL"], os.environ["ETF_SUPABASE_KEY"])
    data = build_data(main_db, etf_db, today, (os.getenv("LINE_WATCH_OWNER") or "").strip() or None)
    path = Path(tempfile.mkdtemp()) / "brief.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    # 上傳前再確認一次：產生的這段時間 Claude 的完整早報剛好完成，就不要蓋掉
    if os.getenv("FORCE") != "1" and not os.getenv("LINE_BRIEF_DRY_RUN") and current_brief_date(url, key) == today:
        print("完整早報剛剛產生了，不上傳簡易版")
        return 0
    sys.argv = ["line_brief.py", str(path)]
    import line_brief
    line_brief.main()
    # 簡易版已經上傳，但仍以錯誤結束：GitHub 會寄信，當天就知道早上的 Claude 排程沒跑
    msg = f"{today} 的完整早報 8:30 前沒有產生，LINE 已改用簡易版；請看早上的 Claude 排程有沒有執行"
    print(f"::error title=完整早報沒有產生::{msg}" if os.getenv("GITHUB_ACTIONS") else msg)
    return 0 if os.getenv("LINE_BRIEF_DRY_RUN") else 1


if __name__ == "__main__":
    sys.exit(main())
