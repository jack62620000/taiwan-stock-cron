"""
暫時的研究腳本（只讀資料庫）：每日選股評分 v3 各加分條件的效果，以及候選新條件。
進場：訊號當天收盤後評分、隔天開盤買；看之後 20 個交易日相對大盤（同樣隔天開盤進場）的超額報酬。
只算 20 日均量 ≥ 500 張的股票，期間 2025-02 起（法人、融資、營收資料齊全）。
"""
import os
import sys
import time

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import signals as S  # noqa: E402
from momentum import STOCK_PATTERN  # noqa: E402

START = "2025-02-03"
H = 20
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 200)


def main():
    from supabase import create_client
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
    all_dates = S.trading_dates(sb)
    recent = [d for d in all_dates if d >= "2024-12-01"]  # 法人、融資只需要評分期間前 40 天
    t = time.time()
    data = {
        "stocks": pd.DataFrame(S._fetch_all(lambda: sb.table("stock_info").select("stock_id").order("stock_id"))),
        "prices": S.load_daily(sb, "daily_prices", "stock_id, date, open_price, high_price, low_price, close_price, volume", all_dates),
        "adjust": pd.DataFrame(S._fetch_all(
            lambda: sb.table("price_adjustments").select("stock_id, date, event_type, factor")
                .gt("date", all_dates[0]).order("date").order("stock_id").order("event_type"))),
        "ins": S.load_daily(sb, "stock_ins_daily", "stock_id, date, foreign_net_shares, sitca_net_shares", recent),
        "margin": S.load_daily(sb, "margin_utilization", "stock_id, date, margin_balance, short_balance, margin_rate", recent),
        "holder": pd.DataFrame(S._fetch_all(
            lambda: sb.table("holder_distribution").select("stock_id, week_date, big_holder_ratio").order("week_date").order("stock_id"))),
        "revenue": pd.DataFrame(S._fetch_all(
            lambda: sb.table("monthly_revenue").select("stock_id, year_month, revenue, yoy_growth, cumulative_yoy_growth")
                .order("year_month").order("stock_id"))),
    }
    idx = pd.DataFrame(S._fetch_all(lambda: sb.table("market_index").select("date, open, close").eq("index_code", "TAIEX").order("date")))
    print(f"載入完成 {time.time() - t:.0f} 秒；股價 {len(data['prices']):,} 筆")
    data["prices"] = data["prices"][data["prices"]["stock_id"].astype(str).str.match(STOCK_PATTERN)]

    feat = S.Features(data)
    close = feat.close_ff
    dates = list(close.index)
    raw = data["prices"]
    adj = S.apply_adjustments(raw, data["adjust"])
    mult = (S._pivot(adj, "close_price") / S._pivot(raw, "close_price")).reindex_like(close).ffill()
    opn = (S._pivot(raw, "open_price").reindex_like(close) * mult).where(lambda x: x > 0)
    high = (S._pivot(raw, "high_price").reindex_like(close) * mult).fillna(close)
    low = (S._pivot(raw, "low_price").reindex_like(close) * mult).fillna(close)
    vol = S._pivot(adj, "volume").reindex_like(close).fillna(0)
    taiex = pd.to_numeric(idx.set_index("date")["close"]).reindex(dates).ffill()
    t_open = pd.to_numeric(idx.set_index("date")["open"]).reindex(dates)

    # 隔天開盤買、持有 20 日，相對大盤
    fwd = (close.shift(-1 - H) / opn.shift(-1) - 1).sub(taiex.shift(-1 - H) / t_open.shift(-1) - 1, axis=0) * 100

    score_dates = [d for d in dates if d >= START]
    sd = pd.Index(score_dates)
    W = lambda df: df.reindex(index=sd, columns=close.columns)  # noqa: E731

    # ===== v3 條件 =====
    vol5_sum = feat.vol5 * 5
    mb, sbal = feat.mb.reindex_like(close), feat.sb.reindex_like(close)
    ratio = (sbal / mb).where(mb > 0)
    hd = pd.DataFrame({d: feat.holder_asof(d)[1] for d in score_dates}).T.reindex(columns=close.columns)
    revs = {d: feat.revenue_asof(d) for d in score_dates}

    def rev_wide(col):
        return pd.DataFrame({d: (r[col] if col in r.columns else pd.Series(dtype=float)) for d, r in revs.items()}).T \
            .reindex(index=sd, columns=close.columns)
    yoy, cum = rev_wide("yoy").astype(float), rev_wide("cum_yoy").astype(float)
    yoy3 = rev_wide("yoy3").fillna(False).astype(bool)
    newhigh = rev_wide("new_high").fillna(False).astype(bool)
    rsi = feat.rsi

    v3 = {
        "外資5日買超 (+4)": (W(feat.f5 > 0), 4),
        "外資買超佔量 ≥5% (+3)": (W((feat.f5 > 0) & (feat.f5 / vol5_sum >= 0.05)), 3),
        "外資連買 ≥3 天 (+3)": (W(feat.f_streak >= 3), 3),
        "投信5日買超 (+7)": (W(feat.s5 > 0), 7),
        "投信連買 ≥3 天 (+8)": (W(feat.s_streak >= 3), 8),
        "大戶週增 >0 (+4)": ((hd > 0) & (hd < 0.5), 4),
        "大戶週增 ≥0.5 (+7)": (hd >= 0.5, 7),
        "券資比 5～10% (+4)": (W((ratio >= 0.05) & (ratio < 0.10)), 4),
        "券資比 ≥10% (+8)": (W(ratio >= 0.10), 8),
        "月營收年增 0～20% (+4)": ((yoy > 0) & (yoy < 20), 4),
        "月營收年增 ≥20% (+8)": (yoy >= 20, 8),
        "累計營收年增 (+4)": (cum > 0, 4),
        "營收連3月年增 (+10)": (yoy3, 10),
        "營收創新高 (+13)": (newhigh, 13),
        "站上月線 (+10)": (W(close > feat.ma20), 10),
        "RSI 50～75 (+4)": (W((rsi >= 50) & (rsi <= 75)), 4),
        "近20日上漲 (+11)": (W(feat.chg20 > 0), 11),
    }

    # ===== 候選新條件 =====
    hi240, lo240 = high.rolling(240, min_periods=200).max(), low.rolling(240, min_periods=200).min()
    pos = (close - lo240) / (hi240 - lo240).replace(0, np.nan) * 100
    rsv = (close - low.rolling(9).min()) / (high.rolling(9).max() - low.rolling(9).min()).replace(0, np.nan) * 100
    K = rsv.ewm(com=2, adjust=False).mean()
    dif = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    sig = dif.ewm(span=9, adjust=False).mean()
    obv = (np.sign(close.diff()).fillna(0) * vol).cumsum()
    p_hi, p_lo = close >= close.rolling(20).max(), close <= close.rolling(20).min()
    o_hi, o_lo = obv >= obv.rolling(20).max(), obv <= obv.rolling(20).min()
    ins = data["ins"]
    foreign = S._pivot(ins, "foreign_net_shares").reindex_like(close).fillna(0)
    sitca = S._pivot(ins, "sitca_net_shares").reindex_like(close).fillna(0)
    f_last5, f_prev5 = foreign.rolling(5).sum(), foreign.rolling(5).sum().shift(5)
    s_last5, s_prev5 = sitca.rolling(5).sum(), sitca.rolling(5).sum().shift(5)
    mb20 = (mb / mb.shift(20) - 1).where(mb.shift(20) > 0)
    cand = {
        "RSI ≥ 70": W(rsi >= 70), "RSI 50～70": W((rsi >= 50) & (rsi < 70)), "RSI ≥ 80（過熱提醒）": W(rsi >= 80),
        "位階 ≥ 70%": W(pos >= 70), "位階 ≥ 90%": W(pos >= 90), "位階 < 30%": W(pos < 30),
        "K ≥ 80": W(K >= 80), "MACD 零軸上": W((dif > 0) & (sig > 0)),
        "OBV 量價齊揚": W(p_hi & o_hi), "OBV 先破底": W(o_lo & ~p_lo),
        "外資由賣轉買": W((f_last5 > 0) & (f_prev5 <= 0)), "投信由賣轉買": W((s_last5 > 0) & (s_prev5 <= 0)),
        "投信買超加速": W((s_last5 > 0) & (s_last5 > s_prev5)),
        "融資 20 日減少且股價上漲": W((mb20 < 0) & (feat.chg20 > 0)),
        "融資 20 日增加且股價下跌": W((mb20 > 0) & (feat.chg20 < 0)),
        "近20日漲 ≥30%（過熱提醒）": W(feat.chg20 >= 0.30),
        "外資投信同步賣超": W((feat.f5 < 0) & (feat.s5 < 0)),
    }

    liquid = W((feat.vol20 >= S.LIQUID_MIN_LOTS * 1000) & opn.shift(-1).notna()) & pd.DataFrame(
        {c: t_open.reindex(sd).shift(-1).notna().values for c in close.columns}, index=sd)
    F = W(fwd)
    half = score_dates[len(score_dates) // 2]
    first = pd.DataFrame({c: sd < half for c in close.columns}, index=sd)

    def lift(name, m):
        m = m.fillna(False).astype(bool)
        on, off = F.where(m & liquid).stack().dropna(), F.where(~m & liquid).stack().dropna()
        row = {"條件": name, "樣本": len(on), "符合": round(on.mean(), 2), "不符合": round(off.mean(), 2),
               "差距": round(on.mean() - off.mean(), 2)}
        for lbl, part in (("前半差距", first), ("後半差距", ~first)):
            a = F.where(m & liquid & part).stack().dropna()
            b = F.where(~m & liquid & part).stack().dropna()
            row[lbl] = round(a.mean() - b.mean(), 2)
        return row

    print(f"\n評分期間 {score_dates[0]} ～ {score_dates[-1]}（{len(score_dates)} 天），前後半以 {half} 分界")
    print("\n===== v3 現有加分條件：符合 vs 不符合，之後 20 日相對大盤（隔天開盤買）=====")
    print(pd.DataFrame([lift(n, m) for n, (m, _) in v3.items()]).set_index("條件").to_string())
    print("\n===== 候選條件 =====")
    print(pd.DataFrame([lift(n, m) for n, m in cand.items()]).set_index("條件").to_string())

    # ===== 前 20 名回測 =====
    def topn(score, n=20, tie=None):
        rows = []
        for d in score_dates:
            ok = liquid.loc[d]
            s = score.loc[d][ok]
            if s.empty:
                continue
            key = s * 1e6 + (tie.loc[d][ok].fillna(0) if tie is not None else 0)
            pick = key.nlargest(n).index
            v = F.loc[d, pick].dropna()
            if len(v):
                rows.append((d, v.mean()))
        r = pd.Series(dict(rows))
        return round(r.mean(), 2), round(r[r.index < half].mean(), 2), round(r[r.index >= half].mean(), 2), round((r > 0).mean() * 100, 1)

    def total(cfg):
        s = pd.DataFrame(0.0, index=sd, columns=close.columns)
        for m, pts in cfg:
            s = s + m.fillna(False).astype(float) * pts
        return s

    base_cfg = [(m, p) for m, p in v3.values()]
    chip_cfg = [(m, p) for n, (m, p) in v3.items() if n.startswith(("外資", "投信", "大戶"))]
    tie = total(chip_cfg) * 1e3 + W(feat.vol20) / 1e6
    no_rsi = [(m, p) for n, (m, p) in v3.items() if not n.startswith("RSI")]
    variants = {
        "v3 現行": base_cfg,
        "拿掉 RSI 50～75": no_rsi,
        "RSI 50～75 → RSI ≥ 70 (+4)": no_rsi + [(cand["RSI ≥ 70"], 4)],
        "RSI 50～75 → 位階 ≥ 70% (+4)": no_rsi + [(cand["位階 ≥ 70%"], 4)],
        "RSI 50～75 → 位階 ≥ 70% (+8)": no_rsi + [(cand["位階 ≥ 70%"], 8)],
        "v3 ＋ OBV 量價齊揚 (+4)": base_cfg + [(cand["OBV 量價齊揚"], 4)],
        "v3 ＋ MACD 零軸上 (+4)": base_cfg + [(cand["MACD 零軸上"], 4)],
        "v3 ＋ 投信由賣轉買 (+4)": base_cfg + [(cand["投信由賣轉買"], 4)],
    }
    rows = []
    for name, cfg in variants.items():
        avg, a1, a2, win = topn(total(cfg), tie=tie)
        rows.append({"版本": name, "前20名平均": avg, "前半": a1, "後半": a2, "贏大盤天數%": win})
    base_all = F.where(liquid).stack().mean()
    print(f"\n===== 每天總分前 20 名之後 20 日相對大盤（隔天開盤買；全部股票平均 {base_all:.2f}%）=====")
    print(pd.DataFrame(rows).set_index("版本").to_string())


if __name__ == "__main__":
    main()
