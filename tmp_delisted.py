"""一次性：找出 2024 年以來下市、不在股票清單的普通股，補進股票清單（標示下市日）並寫入它們的歷史股價。
另外替股票清單裡已經下市的股票標上下市日。"""
import time
import fetch_stock_info as f

def load(table, cols, flt=None):
    out, start = [], 0
    while True:
        q = f.supabase.table(table).select(cols)
        if flt:
            q = flt(q)
        rows = q.range(start, start + 999).execute().data
        out += rows
        if len(rows) < 1000:
            return out
        start += 1000

info = load("stock_info", "stock_id,delisted_date")
known = {r["stock_id"] for r in info}
days = sorted(r["date"] for r in load("daily_prices", "date", lambda q: q.eq("stock_id", "2330").gte("date", "2024-01-01")))
print(f"股票清單 {len(known)} 檔；交易日 {len(days)} 天 {days[0]}～{days[-1]}")

def wanted(sid):
    return len(sid) == 4 and sid.isdigit() and not sid.startswith("0") and sid not in known

found, prices, failed = {}, [], []

def fetch_day(d):
    ok = True
    try:
        data = f.get_json(f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d.replace('-','')}&type=ALLBUT0999&response=json")
        t = next(t for t in data["tables"] if "每日收盤行情" in t.get("title", ""))
        for r in t["data"]:
            sid = r[0].strip()
            if wanted(sid):
                found.setdefault(sid, {"name": r[1].strip(), "market": "上市", "first": d})["last"] = d
                prices.append({"stock_id": sid, "date": d, "open_price": f.parse_price(r[5]), "high_price": f.parse_price(r[6]),
                               "low_price": f.parse_price(r[7]), "close_price": f.parse_price(r[8]), "volume": f.parse_volume(r[2])})
    except Exception as e:
        print(f"  {d} 上市失敗：{e}"); ok = False
    time.sleep(3)
    try:
        data = f.get_json(f"https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={f.tpex_url_date(d)}&type=AL&id=&response=json&order=0&sort=asc", timeout=30)
        if not f.tpex_date_matches(data, d, "上櫃行情"):
            raise ValueError("日期不符")
        for r in data["tables"][0]["data"]:
            sid = r[0].strip()
            if wanted(sid):
                found.setdefault(sid, {"name": r[1].strip(), "market": "上櫃", "first": d})["last"] = d
                prices.append({"stock_id": sid, "date": d, "open_price": f.parse_price(r[4]), "high_price": f.parse_price(r[5]),
                               "low_price": f.parse_price(r[6]), "close_price": f.parse_price(r[2]), "volume": f.parse_volume(r[7])})
    except Exception as e:
        print(f"  {d} 上櫃失敗：{e}"); ok = False
    time.sleep(4)
    return ok

for i, d in enumerate(days, 1):
    if not fetch_day(d):
        failed.append(d)
    if i % 50 == 0:
        print(f"[{i}/{len(days)}] 目前找到 {len(found)} 檔")
for attempt in range(2):
    if not failed:
        break
    print(f"重試 {len(failed)} 天：{failed}")
    time.sleep(60)
    failed = [d for d in failed if not fetch_day(d)]

latest = days[-1]
nxt = {d: days[i + 1] for i, d in enumerate(days[:-1])}
gone = {sid: v for sid, v in found.items() if v["last"] < latest}
still = sorted(sid for sid, v in found.items() if v["last"] >= latest)
print(f"\n找到 {len(found)} 檔不在清單：已下市 {len(gone)}，仍在交易（交給每日更新）{still}")
for sid, v in sorted(gone.items()):
    print(f"  {sid} {v['name']} {v['market']} {v['first']}～{v['last']}")

rows = [{"stock_id": sid, "stock_name": v["name"], "market": v["market"], "industry": None, "shares_issued": None,
         "updated_at": latest, "delisted_date": nxt[v["last"]]} for sid, v in gone.items()]
if rows:
    f.supabase.table("stock_info").upsert(rows).execute()
keep = [p for p in prices if p["stock_id"] in gone]
print(f"寫入 {len(rows)} 檔股票清單、{len(keep)} 筆股價")
_, bad = f.save_data_batches("daily_prices", keep)

# 清單裡已下市的股票：官方現有名單都沒有它，且最後成交日早於最新交易日
listed, otc = f.fetch_listed(), f.fetch_otc()
current = {r["stock_id"] for r in listed + otc}
marked = []
if listed and otc:
    last = {}
    for r in load("daily_prices", "stock_id,date", lambda q: q.gte("date", "2024-01-01")):
        last[r["stock_id"]] = max(last.get(r["stock_id"], ""), r["date"])
    for sid in sorted(known - current):
        ld = last.get(sid)
        if ld and ld < latest:
            f.supabase.table("stock_info").update({"delisted_date": nxt[ld]}).eq("stock_id", sid).execute()
            marked.append((sid, ld))
        else:
            print(f"  {sid} 不在官方名單但最近仍有股價或沒有股價（{ld}），不標示")
print(f"原清單中標示下市 {len(marked)} 檔：{marked}")
print("仍失敗的日子：", failed, "寫入失敗：", bad)
