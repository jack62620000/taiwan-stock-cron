"""
每日選股訊號評分 - 給 GitHub Actions 排程使用，也可以手動回補

每個交易日幫全市場股票打分數，寫進 Supabase 的 daily_signals 表。
總分 100 = 籌碼 25 + 資金 25 + 基本面 25 + 技術面 25，評分規則見下方 score_day()。

用法：
    python signals.py                                   # 評分資料庫裡最新的交易日
    python signals.py --start 2026-06-01                # 從 6/1 回補到最新交易日
    python signals.py --start 2026-06-01 --end 2026-06-30
也可以用環境變數 SIGNALS_START / SIGNALS_END（給 GitHub Actions 手動執行用）。

設計原則：
- 只用「那一天收盤後已經公開」的資料，回補的歷史分數才不會偷看到未來。
  月營收：M 月營收視為 M+1 月 11 日起才可用（法定 10 日前公布）。
  大戶持股：週資料於隔天公布，只用 week_date 早於評分日的資料。
- 分數只用固定規則計算，同一天重跑結果一定一樣，才能回頭檢驗規則準不準。
- 風險提醒（過熱、融資暴增）只提示、不扣分，讓你自己判斷。
"""
import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

LOOKBACK_DAYS = 70          # 評分日之前要多載入的交易日數（MA60、20 日漲幅需要）
LIQUID_MIN_LOTS = 500       # 20 日均量至少 500 張才列入排名
ANCHOR_STOCKS = ["2330", "2317", "2454"]  # 用權值股找交易日，走主鍵索引比較快
BATCH_SIZE = 500


# =====================================================================
# 資料載入
# =====================================================================
def _client():
    from supabase import create_client
    return create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))


def _fetch_all(make_query, page_size=1000):
    rows, start = [], 0
    while True:
        res = make_query().range(start, start + page_size - 1).execute()
        if not res.data:
            break
        rows.extend(res.data)
        if len(res.data) < page_size:
            break
        start += page_size
    return rows


def trading_dates(sb, end=None):
    """資料庫裡所有交易日（由舊到新），可限定不晚於 end"""
    def q():
        query = sb.table("daily_prices").select("date").in_("stock_id", ANCHOR_STOCKS)
        if end:
            query = query.lte("date", end)
        return query.order("date", desc=False).order("stock_id", desc=False)
    return sorted({r["date"] for r in _fetch_all(q)})


def load_daily(sb, table, columns, dates):
    """逐日載入全市場資料（每天一個查詢、走主鍵索引），合併成一張表"""
    def one(d):
        return _fetch_all(lambda: sb.table(table).select(columns).eq("date", d).order("stock_id", desc=False))
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = [r for chunk in pool.map(one, dates) for r in chunk]
    return pd.DataFrame(rows)


def load_inputs(sb, start=None, end=None):
    """
    載入評分需要的全部資料。回傳 (targets, data)：
    targets 是要評分的交易日，data 是 compute_signals() 需要的 dict。
    """
    all_dates = trading_dates(sb, end)
    if not all_dates:
        return [], {}
    end = end or all_dates[-1]
    start = start or end
    targets = [d for d in all_dates if start <= d <= end]
    if not targets:
        return [], {}
    first_idx = all_dates.index(targets[0])
    dates = all_dates[max(0, first_idx - LOOKBACK_DAYS): all_dates.index(targets[-1]) + 1]
    print(f"評分 {len(targets)} 個交易日（{targets[0]} ～ {targets[-1]}），載入 {len(dates)} 天的資料…")

    data = {
        "stocks": pd.DataFrame(_fetch_all(
            lambda: sb.table("stock_info").select("stock_id").order("stock_id", desc=False))),
        "prices": load_daily(sb, "daily_prices", "stock_id, date, close_price, volume", dates),
        "ins": load_daily(sb, "stock_ins_daily", "stock_id, date, foreign_net_shares, sitca_net_shares", dates),
        "margin": load_daily(sb, "margin_utilization",
                             "stock_id, date, margin_balance, short_balance, margin_rate", dates),
        "holder": pd.DataFrame(_fetch_all(
            lambda: sb.table("holder_distribution").select("stock_id, week_date, big_holder_ratio")
                .order("week_date", desc=False).order("stock_id", desc=False))),
        "revenue": pd.DataFrame(_fetch_all(
            lambda: sb.table("monthly_revenue")
                .select("stock_id, year_month, revenue, yoy_growth, cumulative_yoy_growth")
                .order("year_month", desc=False).order("stock_id", desc=False))),
    }
    for name, df in data.items():
        print(f"  {name}: {len(df):,} 筆")
    return targets, data


# =====================================================================
# 特徵計算（全部向量化：日期 × 股票 的寬表）
# =====================================================================
def _pivot(df, value, index="date"):
    if df.empty:
        return pd.DataFrame()
    wide = df.pivot_table(index=index, columns="stock_id", values=value, aggfunc="last")
    return wide.astype(float).sort_index()


def _streak(wide):
    """每格往回數「連續大於 0」幾天"""
    pos = wide > 0
    count = pos.cumsum()
    reset = count.where(~pos).ffill().fillna(0)
    return count - reset


def _rsi(close, period=14):
    """與 app/utils/indicators.py 相同的 RSI 算法（簡單移動平均）"""
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    return 100 - 100 / (1 + gain / loss)


def _revenue_available_from(year_month):
    """M 月營收從 M+1 月 11 日起視為已公布"""
    y, m = map(int, year_month.split("-"))
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return f"{y:04d}-{m:02d}-11"


class Features:
    def __init__(self, data):
        p = data["prices"]
        self.close = _pivot(p, "close_price")
        vol = _pivot(p, "volume")
        self.close_ff = self.close.ffill(limit=5)

        self.ma20 = self.close_ff.rolling(20, min_periods=15).mean()
        self.ma60 = self.close_ff.rolling(60, min_periods=45).mean()
        self.chg1 = self.close_ff / self.close_ff.shift(1) - 1
        self.chg5 = self.close_ff / self.close_ff.shift(5) - 1
        self.chg20 = self.close_ff / self.close_ff.shift(20) - 1
        self.rsi = _rsi(self.close_ff)
        vol0 = vol.fillna(0)
        self.vol5 = vol0.rolling(5).mean()
        self.vol20 = vol0.rolling(20).mean()

        ins = data["ins"]
        foreign = _pivot(ins, "foreign_net_shares").reindex(self.close.index).fillna(0)
        sitca = _pivot(ins, "sitca_net_shares").reindex(self.close.index).fillna(0)
        self.f5 = foreign.rolling(5).sum()
        self.s5 = sitca.rolling(5).sum()
        self.f_streak = _streak(foreign)
        self.s_streak = _streak(sitca)

        m = data["margin"]
        self.mb = _pivot(m, "margin_balance").reindex(self.close.index)
        self.sb = _pivot(m, "short_balance").reindex(self.close.index)
        self.rate = _pivot(m, "margin_rate").reindex(self.close.index)
        prev_mb = self.mb.shift(5)
        self.mb_chg5 = (self.mb / prev_mb - 1).where(prev_mb > 0)

        h = data["holder"]
        self.holder = _pivot(h, "big_holder_ratio", index="week_date")
        self.holder_diff = self.holder.diff()

        r = data["revenue"]
        self.rev = _pivot(r, "revenue", index="year_month")
        self.yoy = _pivot(r, "yoy_growth", index="year_month")
        self.cum_yoy = _pivot(r, "cumulative_yoy_growth", index="year_month")
        self.rev_avail = {ym: _revenue_available_from(ym) for ym in self.rev.index}

        self.stocks = set(data["stocks"]["stock_id"]) if not data["stocks"].empty else set()

    def holder_asof(self, d):
        """評分日之前最新一週的大戶比例與週變化"""
        weeks = [w for w in self.holder.index if w < d]
        if not weeks:
            return pd.Series(dtype=float), pd.Series(dtype=float)
        return self.holder.loc[weeks[-1]], self.holder_diff.loc[weeks[-1]]

    @lru_cache(maxsize=32)
    def revenue_asof(self, d):
        """評分日已公布的月營收：每檔股票最新一期、近 3 期是否都年增、是否創新高"""
        months = tuple(ym for ym in self.rev.index if self.rev_avail[ym] <= d)
        return self._revenue_for_months(months)

    @lru_cache(maxsize=32)
    def _revenue_for_months(self, months):
        cols = ["yoy", "cum_yoy", "yoy3", "new_high", "n_months"]
        if not months:
            return pd.DataFrame(columns=cols, dtype=float)
        rev, yoy, cum = (x.loc[list(months)] for x in (self.rev, self.yoy, self.cum_yoy))
        out = {}
        for sid in rev.columns:
            r = rev[sid].dropna()
            if r.empty:
                continue
            y = yoy[sid].dropna()
            last_ym = r.index[-1]
            prior = r.iloc[:-1]
            out[sid] = {
                "yoy": yoy[sid].get(last_ym, np.nan),
                "cum_yoy": cum[sid].get(last_ym, np.nan),
                "yoy3": len(y) >= 3 and bool((y.tail(3) > 0).all()),
                # 至少要有 3 期舊資料比較，「新高」才有意義
                "new_high": len(prior) >= 3 and r.iloc[-1] > prior.max(),
                "n_months": len(r),
            }
        return pd.DataFrame.from_dict(out, orient="index")


# =====================================================================
# 評分規則
# =====================================================================
def _lots(shares):
    return shares / 1000


def _num(v):
    return v is not None and not (isinstance(v, float) and np.isnan(v))


def score_stock(x):
    """
    單一股票在某一天的評分。x 是該股當天所有特徵的 dict。
    回傳 (chip, margin, fund, tech, reasons, warnings)
    """
    reasons, warnings = [], []

    # ---------- 籌碼面（25）----------
    chip = 0
    f5, s5 = x["f5"], x["s5"]
    if f5 > 0:
        chip += 4
        reasons.append(f"外資5日買超 {_lots(f5):,.0f} 張")
        vol5_sum = x["vol5"] * 5
        if vol5_sum > 0 and f5 / vol5_sum >= 0.05:
            chip += 3
            reasons.append(f"外資買超佔成交量 {f5 / vol5_sum:.0%}")
    if x["f_streak"] >= 3:
        chip += 3
        reasons.append(f"外資連買 {int(x['f_streak'])} 天")
    if s5 > 0:
        chip += 4
        reasons.append(f"投信5日買超 {_lots(s5):,.0f} 張")
    if x["s_streak"] >= 3:
        chip += 4
        reasons.append(f"投信連買 {int(x['s_streak'])} 天")
    hd = x["holder_diff"]
    if _num(hd) and hd > 0:
        chip += 7 if hd >= 0.5 else 4
        reasons.append(f"大戶持股週增 {hd:.2f} 個百分點")

    # ---------- 資金面（25）----------
    margin = 0
    rate, mb, mb_chg5 = x["rate"], x["mb"], x["mb_chg5"]
    if not _num(rate):
        margin += 4                                   # 無融資資料，給中性分
    elif rate < 10:
        margin += 8
        reasons.append(f"融資使用率低（{rate:.1f}%）")
    elif rate < 20:
        margin += 4
    if not _num(mb_chg5):
        margin += 4                                   # 無融資資料，給中性分
    elif mb_chg5 < 0 and x["chg5"] > 0:
        margin += 9
        reasons.append(f"資減價漲（融資5日 {mb_chg5:+.1%}、股價 {x['chg5']:+.1%}）")
    elif mb_chg5 <= 0.05:
        margin += 4
    if _num(mb) and mb > 0 and _num(x["sb"]):
        ratio = x["sb"] / mb
        if ratio >= 0.10:
            margin += 8
            reasons.append(f"券資比 {ratio:.0%}")
        elif ratio >= 0.05:
            margin += 4

    # ---------- 基本面（25）----------
    fund = 0
    yoy, cum = x["yoy"], x["cum_yoy"]
    if _num(yoy) and yoy > 0:
        fund += 10 if yoy >= 20 else 5
        reasons.append(f"月營收年增 {yoy:.1f}%")
    if _num(cum) and cum > 0:
        fund += 4
        reasons.append(f"累計營收年增 {cum:.1f}%")
    if x["yoy3"] is True:
        fund += 5
        reasons.append("營收連3月年增")
    if x["new_high"] is True:
        fund += 6
        reasons.append(f"營收創近{int(x['n_months'])}月新高")

    # ---------- 技術面（25）----------
    tech = 0
    close, ma20, ma60 = x["close"], x["ma20"], x["ma60"]
    above20 = _num(ma20) and close > ma20
    above60 = _num(ma60) and close > ma60
    if above20:
        tech += 5
    if above60:
        tech += 5
    if above20 and above60:
        reasons.append("站上月線與季線")
    elif above20:
        reasons.append("站上月線")
    elif above60:
        reasons.append("站上季線")
    if _num(ma20) and _num(ma60) and ma20 > ma60:
        tech += 4
        reasons.append("均線多頭排列")
    if x["vol20"] > 0 and x["vol5"] / x["vol20"] >= 1.5 and _num(x["chg1"]) and x["chg1"] > 0:
        tech += 5
        reasons.append(f"量增價漲（5日均量為20日的 {x['vol5'] / x['vol20']:.1f} 倍）")
    rsi = x["rsi"]
    if _num(rsi) and 50 <= rsi <= 75:
        tech += 3
    if _num(x["chg20"]) and x["chg20"] > 0:
        tech += 3

    # ---------- 風險提醒（不扣分）----------
    if _num(rsi) and rsi >= 80:
        warnings.append(f"RSI 過熱（{rsi:.0f}）")
    if _num(x["chg20"]) and x["chg20"] >= 0.30:
        warnings.append(f"近20日已漲 {x['chg20']:.0%}")
    if _num(rate) and rate >= 30:
        warnings.append(f"融資使用率偏高（{rate:.1f}%）")
    if _num(mb_chg5) and mb_chg5 >= 0.20:
        warnings.append(f"融資5日暴增 {mb_chg5:.0%}")
    if f5 < 0 and s5 < 0:
        warnings.append("外資投信同步賣超")

    return chip, margin, fund, tech, reasons, warnings


def score_day(feat, d):
    """某一個交易日全市場的評分，回傳要寫進 daily_signals 的資料列"""
    if d not in feat.close.index:
        return []
    close_today = feat.close.loc[d]
    traded = close_today.dropna().index
    holder, holder_diff = feat.holder_asof(d)
    rev = feat.revenue_asof(d)

    # 先把每張寬表當天那一列取出來，逐檔查詢時就只是查 dict
    day = {}
    for name in ("ma20", "ma60", "chg1", "chg5", "chg20", "rsi", "vol5", "vol20",
                 "f5", "s5", "f_streak", "s_streak", "mb", "sb", "rate", "mb_chg5"):
        wide = getattr(feat, name)
        day[name] = wide.loc[d].to_dict() if (not wide.empty and d in wide.index) else {}

    def at(name, sid):
        return day[name].get(sid, np.nan)

    rows = []
    for sid in traded:
        if feat.stocks and sid not in feat.stocks:
            continue
        r = rev.loc[sid] if sid in rev.index else None
        x = {
            "close": close_today[sid],
            "ma20": at("ma20", sid), "ma60": at("ma60", sid),
            "chg1": at("chg1", sid), "chg5": at("chg5", sid), "chg20": at("chg20", sid),
            "rsi": at("rsi", sid),
            "vol5": np.nan_to_num(at("vol5", sid)), "vol20": np.nan_to_num(at("vol20", sid)),
            "f5": np.nan_to_num(at("f5", sid)), "s5": np.nan_to_num(at("s5", sid)),
            "f_streak": np.nan_to_num(at("f_streak", sid)),
            "s_streak": np.nan_to_num(at("s_streak", sid)),
            "mb": at("mb", sid), "sb": at("sb", sid),
            "rate": at("rate", sid), "mb_chg5": at("mb_chg5", sid),
            "holder_diff": holder_diff.get(sid, np.nan),
            "yoy": r["yoy"] if r is not None else np.nan,
            "cum_yoy": r["cum_yoy"] if r is not None else np.nan,
            "yoy3": bool(r["yoy3"]) if r is not None else False,
            "new_high": bool(r["new_high"]) if r is not None else False,
            "n_months": r["n_months"] if r is not None else 0,
        }
        chip, margin, fund, tech, reasons, warnings = score_stock(x)
        lots20 = x["vol20"] / 1000
        rows.append({
            "stock_id": sid,
            "date": d,
            "total_score": chip + margin + fund + tech,
            "chip_score": chip,
            "margin_score": margin,
            "fund_score": fund,
            "tech_score": tech,
            "reasons": reasons,
            "warnings": warnings,
            "close_price": float(x["close"]),
            "change_5d": round(float(x["chg5"]) * 100, 2) if _num(x["chg5"]) else None,
            "change_20d": round(float(x["chg20"]) * 100, 2) if _num(x["chg20"]) else None,
            "avg_volume_20": int(round(lots20)),
            "liquid": bool(lots20 >= LIQUID_MIN_LOTS),
        })

    # 排名：只排有流動性的股票，同分時籌碼分高、成交量大的在前
    liquid = sorted(
        (r for r in rows if r["liquid"]),
        key=lambda r: (-r["total_score"], -r["chip_score"], -r["avg_volume_20"], r["stock_id"]),
    )
    for i, r in enumerate(liquid, start=1):
        r["rank"] = i
    for r in rows:
        r.setdefault("rank", None)
    return rows


def compute_signals(targets, data):
    feat = Features(data)
    return {d: score_day(feat, d) for d in targets}


# =====================================================================
# 主程式
# =====================================================================
def _error(msg):
    """印出錯誤；在 GitHub Actions 上同時顯示成錯誤註記"""
    print(f"❌ {msg}")
    if os.getenv("GITHUB_ACTIONS"):
        print("::error title=signals.py::" + str(msg).replace("%", "%25").replace("\n", "%0A"))


def save(sb, rows):
    failed = 0
    for i in range(0, len(rows), BATCH_SIZE):
        try:
            sb.table("daily_signals").upsert(rows[i:i + BATCH_SIZE]).execute()
        except Exception as e:
            failed += len(rows[i:i + BATCH_SIZE])
            _error(f"寫入 daily_signals 失敗（{rows[i]['date']}，Index {i}）：{e}")
    return failed


def main():
    parser = argparse.ArgumentParser(description="每日選股訊號評分")
    parser.add_argument("--start", default=os.getenv("SIGNALS_START", "").strip() or None)
    parser.add_argument("--end", default=os.getenv("SIGNALS_END", "").strip() or None)
    args = parser.parse_args()

    sb = _client()
    targets, data = load_inputs(sb, args.start, args.end)
    if not targets:
        _error("找不到要評分的交易日")
        return 1

    results = compute_signals(targets, data)
    failed = 0
    for d, rows in results.items():
        if not rows:
            print(f"  {d}：沒有可評分的股票，略過")
            continue
        failed += save(sb, rows)
        top = sorted((r for r in rows if r["rank"]), key=lambda r: r["rank"])[:5]
        top_text = "、".join(f"{r['stock_id']}({r['total_score']})" for r in top)
        print(f"  {d}：{len(rows)} 檔，前五名 {top_text}")

    if failed:
        _error(f"有 {failed} 筆寫入失敗")
        return 1
    print("✅ 評分完成")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        tb = traceback.format_exc()
        print(tb)
        # 在 GitHub Actions 上同時顯示成錯誤註記，不用下載整份 log 也看得到原因
        if os.getenv("GITHUB_ACTIONS"):
            print("::error title=signals.py 執行失敗::" + tb.strip().replace("%", "%25").replace("\n", "%0A"))
        sys.exit(1)
