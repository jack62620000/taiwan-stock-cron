"""
每日選股訊號評分 - 給 GitHub Actions 排程使用，也可以手動回補

每個交易日幫全市場股票打分數，寫進 Supabase 的 daily_signals 表。
總分 100 = 籌碼 32 + 資金 8 + 基本面 35 + 技術面 25，評分規則見下方 score_stock()。

規則版本（RULE_VERSION）：
  v1（2026-10-14）：四面向各 25 分。
  v2（2026-10-14）：用 6～8 月資料設計、9～10 月資料驗證後調整：
      - 拿掉「站上季線」「均線多頭排列」「量增價漲」：這三項在兩段期間都讓之後的報酬變差
      - 營收連 3 月年增、營收創新高加重（兩段期間都有正向效果），基本面改為 35 分
      - 「近 20 日已大漲」不扣分：資料顯示大漲股之後並沒有比較差，只保留風險提醒
  v3（2026-10-17）：法人、融資、月營收補到 2025 年後，用 2025-02～2026-09（約 400 個交易日）重新驗證：
      - 拿掉「融資使用率低」「資減價漲」（含沒有資料時的中性分）：沒有效果，最近一年甚至是反效果
      - 空出的 17 分加到兩段期間都有效的因子：投信 5 日買超 +3、投信連買 +4、站上月線 +4、近 20 日上漲 +6
      - 每天總分前 20 名之後 20 日的超額報酬：v2 +4.26%，v3 +4.90%
  v4（2026-10-21）：改用「隔天開盤買」驗證（2025-02～2026-10），技術面「RSI 50～75」改成「RSI ≥ 70」：
      - RSI 70 以上的股票之後反而比較強（前後兩段都多約 1.2～1.8 個百分點），50～75 的效果最弱
      - 每天總分前 20 名之後 20 日比全體平均多：v3 4.49%，v4 4.83%（前後兩段都比 v3 好）
  v5（2026-10-22）：同樣用隔天開盤買驗證，大戶持股改看「近 4 週增加 ≥ 1 個百分點」（+7）：
      - 原本的「大戶週增 0～0.5」（+4）整體和後段都是負效果；看 4 週變化比看 1 週穩定（前後兩段都多 2.5～5 個百分點）
      - 每天總分前 20 名比全體平均多：v4 4.83%，v5 4.95%；前 10 名 v4 5.40%，v5 5.78%（前後兩段都比 v4 好）
      - 同時試過券資比改級距、投信連買 5 天再加分，都沒有變好，所以沒有採用

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
- 技術面用還原權值後的股價計算（price_adjustments），除權息、減資、變更面額不會造成假漲跌；
  寫進資料表的 close_price 仍是原始收盤價。
"""
import argparse
import os
import sys
from functools import lru_cache

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from retry import execute as _execute

load_dotenv()

RULE_VERSION = 5
LOOKBACK_DAYS = 30          # 評分日之前要多載入的交易日數（MA20、RSI、20 日漲幅需要）
LIQUID_MIN_LOTS = 500       # 20 日均量至少 500 張才列入排名
ANCHOR_STOCKS = ["2330", "2317", "2454"]  # 用權值股找交易日，走主鍵索引比較快
BATCH_SIZE = 500
KEEP_FULL_DAYS = 20         # 最近這麼多個評分日保留完整資料；更早的只留回測需要的股票、清空加分理由
BACKTEST_MAX_HORIZON = 20   # 規則回測最長持有天數，精簡時要保留這段期間內曾列入排名的股票


# =====================================================================
# 資料載入
# =====================================================================
def _client():
    from supabase import create_client
    return create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))


def _fetch_all(make_query, page_size=1000):
    rows, start = [], 0
    while True:
        res = _execute(lambda: make_query().range(start, start + page_size - 1))
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
    """
    逐日載入全市場資料（每天一個查詢、走主鍵索引），合併成一張表。
    依序查詢、不平行：同一個連線同時送太多請求，Supabase 會中斷連線。
    """
    rows = []
    for d in dates:
        rows.extend(_fetch_all(
            lambda d=d: sb.table(table).select(columns).eq("date", d).order("stock_id", desc=False)))
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
        # 還原權值事件：只取評分區間內、不晚於最後評分日的事件，不會用到未來的資料
        "adjust": pd.DataFrame(_fetch_all(
            lambda: sb.table("price_adjustments").select("stock_id, date, event_type, factor")
                .gt("date", dates[0]).lte("date", targets[-1])
                .order("date", desc=False).order("stock_id", desc=False).order("event_type", desc=False))),
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
# 還原權值
# =====================================================================
VOLUME_EVENTS = ("變更面額",)  # 股數等比例變動的事件，成交量也要還原


def apply_adjustments(prices, events):
    """
    與 app/utils/db.py 的 apply_adjustments 相同：事件日之前的股價乘上之後所有事件的
    factor（參考價 ÷ 事件前收盤價），讓均線、漲幅、RSI 不會被除權息、減資、變更面額的假跳空扭曲。
    """
    if events is None or events.empty or prices.empty:
        return prices
    df = prices.copy()
    ev = events[["stock_id", "date", "event_type", "factor"]].copy()
    ev["factor"] = pd.to_numeric(ev["factor"], errors="coerce")
    ev = ev[ev["factor"] > 0]
    rows = df[["stock_id", "date"]].rename_axis("_row").reset_index().merge(ev, on="stock_id", suffixes=("", "_ev"))
    rows = rows[pd.to_datetime(rows["date"]) < pd.to_datetime(rows["date_ev"])]
    price_mult = rows.groupby("_row")["factor"].prod().reindex(df.index, fill_value=1.0)
    vol_rows = rows[rows["event_type"].isin(VOLUME_EVENTS)]
    vol_mult = (1 / vol_rows.groupby("_row")["factor"].prod()).reindex(df.index, fill_value=1.0)
    df["close_price"] = pd.to_numeric(df["close_price"], errors="coerce") * price_mult
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce") * vol_mult
    return df


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
        raw = data["prices"]
        self.close_raw = _pivot(raw, "close_price")  # 寫進資料表的收盤價維持原始價格
        p = apply_adjustments(raw, data.get("adjust"))
        self.close = _pivot(p, "close_price")
        vol = _pivot(p, "volume")
        self.close_ff = self.close.ffill(limit=5)

        self.ma20 = self.close_ff.rolling(20, min_periods=15).mean()
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
        self.holder_diff4 = self.holder.diff(4)  # 近 4 週變化（百分點）

        r = data["revenue"]
        self.rev = _pivot(r, "revenue", index="year_month")
        self.yoy = _pivot(r, "yoy_growth", index="year_month")
        self.cum_yoy = _pivot(r, "cumulative_yoy_growth", index="year_month")
        self.rev_avail = {ym: _revenue_available_from(ym) for ym in self.rev.index}

        self.stocks = set(data["stocks"]["stock_id"]) if not data["stocks"].empty else set()

    def holder_asof(self, d):
        """評分日之前最新一週的大戶比例與近 4 週變化"""
        weeks = [w for w in self.holder.index if w < d]
        if not weeks:
            return pd.Series(dtype=float), pd.Series(dtype=float)
        return self.holder.loc[weeks[-1]], self.holder_diff4.loc[weeks[-1]]

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
        # pivot 會丟掉整列（或整欄）都是空值的月份與股票，用 reindex 補回空值，避免查不到
        rev = self.rev.reindex(index=list(months))
        yoy, cum = (x.reindex(index=list(months), columns=rev.columns) for x in (self.yoy, self.cum_yoy))
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

    # ---------- 籌碼面（32）----------
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
        chip += 7
        reasons.append(f"投信5日買超 {_lots(s5):,.0f} 張")
    if x["s_streak"] >= 3:
        chip += 8
        reasons.append(f"投信連買 {int(x['s_streak'])} 天")
    # v5：大戶看近 4 週變化，增加 1 個百分點以上才加分（週變化小幅增加回測是負效果）
    hd4 = x["holder_diff4"]
    if _num(hd4) and hd4 >= 1:
        chip += 7
        reasons.append(f"大戶持股 4 週增 {hd4:.2f} 個百分點")

    # ---------- 資金面（8）----------
    # v3 拿掉「融資使用率低」「資減價漲」：回測沒有效果，只留券資比
    margin = 0
    rate, mb, mb_chg5 = x["rate"], x["mb"], x["mb_chg5"]
    if _num(mb) and mb > 0 and _num(x["sb"]):
        ratio = x["sb"] / mb
        if ratio >= 0.10:
            margin += 8
            reasons.append(f"券資比 {ratio:.0%}")
        elif ratio >= 0.05:
            margin += 4

    # ---------- 基本面（35）----------
    # 營收連續成長、創新高是兩段回測期間都最穩定的正向因子，所以權重最高
    fund = 0
    yoy, cum = x["yoy"], x["cum_yoy"]
    if _num(yoy) and yoy > 0:
        fund += 8 if yoy >= 20 else 4
        reasons.append(f"月營收年增 {yoy:.1f}%")
    if _num(cum) and cum > 0:
        fund += 4
        reasons.append(f"累計營收年增 {cum:.1f}%")
    if x["yoy3"] is True:
        fund += 10
        reasons.append("營收連3月年增")
    if x["new_high"] is True:
        fund += 13
        reasons.append(f"營收創近{int(x['n_months'])}月新高")

    # ---------- 技術面（25）----------
    # v1 的「站上季線」「均線多頭排列」「量增價漲」回測都是負效果，已拿掉
    tech = 0
    close, ma20 = x["close"], x["ma20"]
    if _num(ma20) and close > ma20:
        tech += 10
        reasons.append("站上月線")
    rsi = x["rsi"]
    if _num(rsi) and rsi >= 70:
        tech += 4
        reasons.append(f"RSI 強勢（{rsi:.0f}）")
    if _num(x["chg20"]) and x["chg20"] > 0:
        tech += 11
        reasons.append(f"近20日上漲 {x['chg20']:.1%}")

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
    close_raw = feat.close_raw.loc[d]
    traded = close_today.dropna().index
    holder, holder_diff4 = feat.holder_asof(d)
    rev = feat.revenue_asof(d)

    # 先把每張寬表當天那一列取出來，逐檔查詢時就只是查 dict
    day = {}
    for name in ("ma20", "chg5", "chg20", "rsi", "vol5", "vol20",
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
            "ma20": at("ma20", sid),
            "chg5": at("chg5", sid), "chg20": at("chg20", sid),
            "rsi": at("rsi", sid),
            "vol5": np.nan_to_num(at("vol5", sid)), "vol20": np.nan_to_num(at("vol20", sid)),
            "f5": np.nan_to_num(at("f5", sid)), "s5": np.nan_to_num(at("s5", sid)),
            "f_streak": np.nan_to_num(at("f_streak", sid)),
            "s_streak": np.nan_to_num(at("s_streak", sid)),
            "mb": at("mb", sid), "sb": at("sb", sid),
            "rate": at("rate", sid), "mb_chg5": at("mb_chg5", sid),
            "holder_diff4": holder_diff4.get(sid, np.nan),
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
            "close_price": float(close_raw[sid]),
            "change_5d": round(float(x["chg5"]) * 100, 2) if _num(x["chg5"]) else None,
            "change_20d": round(float(x["chg20"]) * 100, 2) if _num(x["chg20"]) else None,
            "avg_volume_20": int(round(lots20)),
            "liquid": bool(lots20 >= LIQUID_MIN_LOTS),
            "rule_version": RULE_VERSION,
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
        batch = rows[i:i + BATCH_SIZE]
        try:
            _execute(lambda: sb.table("daily_signals").upsert(batch))
        except Exception as e:
            failed += len(batch)
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

    # 精簡舊資料：KEEP_FULL_DAYS 個評分日以前的資料，刪掉回測用不到的冷門股、清空加分理由
    # 只處理剛超過保留期的幾天（回補了多少天就多處理幾天），每次都很快
    try:
        res = _execute(lambda: sb.rpc("prune_daily_signals", {
            "keep_days": KEEP_FULL_DAYS,
            "lookback": BACKTEST_MAX_HORIZON + 1,  # 回測是評分隔天買進，賣出日在評分日後 horizon + 1 個評分日
            "max_dates": len(targets) + 5,
        }))
        print(f"  精簡舊評分：刪除 {res.data} 筆冷門股資料")
    except Exception as e:
        _error(f"精簡舊評分失敗（評分已寫入，不影響今天的結果）：{e}")
        failed += 1

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
