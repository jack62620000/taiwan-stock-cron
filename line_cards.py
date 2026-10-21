"""
LINE 打字查詢用的個股分析卡 - 每晚評分完，把全部股票的分析卡算好，存到 Supabase Storage

LINE 機器人（supabase/functions/line-webhook）收到股票代號或名稱時，直接讀這裡算好的卡片回覆，
不用自己計算；卡片和每日推播是同一個 line_push.stock_bubble，數字和網頁「個股分析卡」頁相同。

存放位置（不佔資料庫空間，Storage 另外計算，每天覆蓋只留最新一份）：
    archive 桶 / _line_cards / {代號前兩碼}.json   同一個前兩碼的股票放在同一個檔案，{代號: 卡片}
    archive 桶 / _line_cards / index.json          {"date": 評分日, "stocks": {代號: 名稱}}
    archive 桶 / _line_cards / granville.json、momentum.json   選單「葛蘭碧」「動能股」的回覆（見 line_lists.py）

和個股分析卡頁逐檔查詢不同，這裡整批載入全市場資料再依股票切開（逐檔查 2,000 檔太慢）；
取的筆數和分析卡頁相同（股價、法人近 250 個交易日，季報 8 季、營收 12 個月、評分 21 個評分日）。
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app"))

import line_push  # noqa: E402
from line_lists import build_lists  # noqa: E402

BUCKET = "archive"
PREFIX = "_line_cards"
PRICE_DAYS = 250   # 同個股分析卡：get_daily_prices(limit=250)、get_stock_ins(limit=250)
EXTRA_DAYS = 20    # 多抓幾天，停牌過的股票也湊得到 250 筆
UPLOAD_WORKERS = 4


def shard_of(stock_id):
    return str(stock_id)[:2]


def _tail(df, order, n):
    """每檔股票依 order 排序後取最後 n 筆"""
    if df.empty:
        return {}
    df = df.sort_values(["stock_id"] + order)
    return {sid: g.tail(n).reset_index(drop=True) for sid, g in df.groupby("stock_id", sort=False)}


def load_all():
    """整批載入全市場的分析卡資料，回傳 dict（各表依股票切好）；還沒有股價或評分資料時回傳 None"""
    from utils.db import (
        apply_adjustments, get_price_adjustments, get_recent_dates, get_signal_dates,
        get_rows_for_stocks, get_snapshots, get_stock_list, supabase, _fetch_all,
    )
    stocks = get_stock_list()
    ids = stocks["stock_id"].tolist()
    dates = get_recent_dates("daily_prices", n=PRICE_DAYS + EXTRA_DAYS)
    sig_dates = get_signal_dates(line_push.SCORE_RECENT + 1)
    if not dates or not sig_dates:
        return None
    print(f"載入 {len(ids):,} 檔、{len(dates)} 個交易日（{min(dates)}～{max(dates)}）…")

    prices = get_snapshots("daily_prices", dates)
    prices = apply_adjustments(prices, get_price_adjustments(None, prices["date"].min()))
    ins = get_snapshots("stock_ins_daily", dates)
    margin = get_snapshots("margin_utilization", get_recent_dates("margin_utilization", n=5))
    holder = get_snapshots("holder_distribution", get_recent_dates("holder_distribution", "week_date", n=3),
                           date_col="week_date")
    year = date.fromisoformat(max(dates)).year
    revenue = pd.DataFrame(_fetch_all(lambda: supabase.table("monthly_revenue").select("*")
                                      .gte("year_month", f"{year - 2}-01").order("year_month").order("stock_id")))
    quarterly = pd.DataFrame(_fetch_all(lambda: supabase.table("quarterly_report").select("*")
                                        .gte("year", year - 3).order("year").order("quarter").order("stock_id")))
    signals = get_snapshots("daily_signals", sig_dates)
    momentum = get_rows_for_stocks("momentum_signals", ids, since=dates[min(9, len(dates) - 1)])  # 近 10 個交易日
    for name, df in [("股價", prices), ("法人", ins), ("融資融券", margin), ("大戶", holder), ("營收", revenue),
                     ("季報", quarterly), ("評分", signals), ("動能", momentum)]:
        print(f"  {name}：{len(df):,} 筆")

    return {
        "stocks": stocks,
        "sig_date": sig_dates[0],
        "prices": _tail(prices, ["date"], PRICE_DAYS),
        "ins": _tail(ins, ["date"], PRICE_DAYS),
        "margin": _tail(margin, ["date"], 1),
        "holder": _tail(holder, ["week_date"], 1),
        "revenue": _tail(revenue, ["year_month"], 12),
        "quarterly": _tail(quarterly, ["year", "quarter"], 8),
        "signals": _tail(signals, ["date"], line_push.SCORE_RECENT + 1),
        "momentum": {r["stock_id"]: r for r in momentum.sort_values("date").groupby("stock_id").tail(1)
                     .to_dict("records")} if not momentum.empty else {},
    }


def analyze_all(data):
    """
    每檔股票的分析卡內容（和 line_push.analyze_stock 相同的計算，只是資料整批載入）。
    葛蘭碧一次算全部股票，比逐檔快很多。回傳 {代號: analysis dict}
    """
    from utils.analysis import granville_status
    from utils.card import card_metrics
    from utils.momentum import momentum_summary
    empty = pd.DataFrame()
    info = {r["stock_id"]: r for r in data["stocks"].to_dict("records")}
    ids = [sid for sid in info if sid in data["prices"]]

    cols = ["stock_id", "date", "high_price", "low_price", "close_price", "volume"]
    p_all = pd.concat([data["prices"][sid][cols] for sid in ids], ignore_index=True)
    i_frames = [data["ins"][sid][["stock_id", "date", "foreign_net_shares"]] for sid in ids if sid in data["ins"]]
    i_all = pd.concat(i_frames, ignore_index=True) if i_frames else empty
    gran = granville_status(ids, p_all, i_all)
    gran = {r["代號"]: r for r in gran.to_dict("records")} if not gran.empty else {}

    out = {}
    for sid in ids:
        d = {key: data[key].get(sid, empty) for key in ("prices", "quarterly", "revenue", "ins", "margin", "holder", "signals")}
        try:
            m = card_metrics(d["prices"], d["quarterly"], d["revenue"], d["ins"], d["margin"], d["holder"], d["margin"])
        except Exception as e:  # 單一檔資料有問題時不影響其他股票
            print(f"  {sid} 算不出分析卡：{type(e).__name__}: {e}")
            continue
        mrow = data["momentum"].get(sid)
        out[sid] = {"info": info[sid], "m": m, "gran": gran.get(sid), "hist": d["signals"],
                    "mom": momentum_summary(mrow) if mrow is not None else None}
    return out


def build_shards(analyses):
    """依代號前兩碼分檔：{前兩碼: {代號: 卡片}}"""
    shards = {}
    for sid, a in analyses.items():
        shards.setdefault(shard_of(sid), {})[sid] = line_push.stock_bubble(sid, a)
    return shards


def upload(sb, shards, index, extra=None):
    """上傳分檔、extra（{檔名: 內容}，例如葛蘭碧、動能股清單），最後才換索引"""
    bucket = sb.storage.from_(BUCKET)
    files = {f"{PREFIX}/{k}.json": v for k, v in shards.items()}
    files.update({f"{PREFIX}/{k}": v for k, v in (extra or {}).items()})
    files[f"{PREFIX}/index.json"] = index  # 最後才換索引，查詢時不會讀到還沒上傳的分檔

    def put(item):
        path, content = item
        data = json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode()
        for attempt in range(3):
            try:
                bucket.upload(path, data, {"content-type": "application/json", "upsert": "true"})
                return len(data)
            except Exception:
                if attempt == 2:
                    raise
    items = list(files.items())
    with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as pool:
        sizes = list(pool.map(put, items[:-1]))
    sizes.append(put(items[-1]))
    return sum(sizes)


def stored_date(sb):
    """Storage 裡現有卡片的評分日；還沒有卡片時回傳 None"""
    try:
        return json.loads(sb.storage.from_(BUCKET).download(f"{PREFIX}/index.json")).get("date")
    except Exception:
        return None


def is_stale(sb):
    """卡片的評分日比最新評分日舊（例如前一晚產生卡片那一步失敗）"""
    from utils.db import get_signal_dates
    latest = (get_signal_dates(1) or [None])[0]
    current = stored_date(sb)
    print(f"最新評分日 {latest}，現有卡片 {current or '無'}")
    return latest is not None and current != latest


def run(sb, dry_run=False):
    data = load_all()
    if data is None:
        print("還沒有評分資料，不產生卡片")
        return 0
    analyses = analyze_all(data)
    shards = build_shards(analyses)
    names = {sid: a["info"].get("stock_name") or "" for sid, a in analyses.items()}
    index = {"date": data["sig_date"], "stocks": names}
    lists = build_lists()
    for name, content in lists.items():
        print(f"  {name}：資料日期 {content['date']}，{len(content['messages'])} 則訊息")
    if dry_run:
        size = sum(len(json.dumps(v, ensure_ascii=False).encode()) for v in shards.values())
        print(f"試跑：{len(analyses):,} 檔、{len(shards)} 個分檔，共 {size / 1e6:.1f} MB（未上傳）")
        return len(analyses)
    size = upload(sb, shards, index, lists)
    print(f"已更新 LINE 查詢卡片（評分日 {data['sig_date']}）：{len(analyses):,} 檔、{len(shards)} 個分檔，"
          f"共 {size / 1e6:.1f} MB")
    return len(analyses)


def main():
    from utils.db import supabase
    # 早上的補抓排程沒有重新評分時用：卡片已經是最新的就不重算
    if os.getenv("LINE_CARDS_IF_STALE") == "1" and not is_stale(supabase):
        print("卡片已是最新，不用重新產生")
        return 0
    run(supabase, dry_run=os.getenv("LINE_DRY_RUN") == "1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
