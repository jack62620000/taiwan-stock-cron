"""
暫時的研究腳本（只讀資料庫）：分析卡籌碼面、資金面、營收的趨勢標籤，以及葛蘭碧八大法則，
用「隔天開盤買」看之後 10／20 個交易日相對大盤（大盤也用隔天開盤）的超額報酬。
只算 20 日均量 ≥ 500 張的股票，期間 2025-02 起（法人、融資、營收資料齊全）。
"""
import os
import sys
import time

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "app"))

import signals as S  # noqa: E402
from momentum import STOCK_PATTERN  # noqa: E402

START = "2025-02-03"
LIQ = 500 * 1000
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 300)


def main():
    from supabase import create_client
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
    # utils.granville 不連資料庫，可以直接 import
    from utils.granville import rule_frames, confirm_frames, RULES

    all_dates = S.trading_dates(sb)
    recent = [d for d in all_dates if d >= "2024-12-01"]
    t = time.time()
    prices = S.load_daily(sb, "daily_prices", "stock_id, date, open_price, close_price, volume", recent)
    prices = prices[prices["stock_id"].astype(str).str.match(STOCK_PATTERN)]
    adjust = pd.DataFrame(S._fetch_all(
        lambda: sb.table("price_adjustments").select("stock_id, date, event_type, factor")
            .gt("date", recent[0]).order("date").order("stock_id").order("event_type")))
    ins = S.load_daily(sb, "stock_ins_daily", "stock_id, date, foreign_net_shares, sitca_net_shares, dealers_net_shares", recent)
    margin = S.load_daily(sb, "margin_utilization", "stock_id, date, margin_balance, short_balance", recent)
    holder = pd.DataFrame(S._fetch_all(
        lambda: sb.table("holder_distribution").select("stock_id, week_date, big_holder_ratio").order("week_date").order("stock_id")))
    revenue = pd.DataFrame(S._fetch_all(
        lambda: sb.table("monthly_revenue").select("stock_id, year_month, revenue, yoy_growth, cumulative_yoy_growth")
            .gte("year_month", "2023-01").order("year_month").order("stock_id")))
    idx = pd.DataFrame(S._fetch_all(lambda: sb.table("market_index").select("date, open, close").eq("index_code", "TAIEX").order("date")))
    print(f"載入完成 {time.time() - t:.0f} 秒")

    pv = S._pivot
    adj = S.apply_adjustments(prices, adjust)
    close = pv(adj, "close_price").ffill(limit=5)
    mult = (pv(adj, "close_price") / pv(prices, "close_price")).reindex_like(close).ffill()
    opn = (pv(prices, "open_price").reindex_like(close) * mult).where(lambda x: x > 0)
    vol = pv(adj, "volume").reindex_like(close).fillna(0)
    dates = list(close.index)
    taiex = pd.to_numeric(idx.set_index("date")["close"]).reindex(dates).ffill()
    t_open = pd.to_numeric(idx.set_index("date")["open"]).reindex(dates)

    def fwd(h):
        return (close.shift(-1 - h) / opn.shift(-1) - 1).sub(taiex.shift(-1 - h) / t_open.shift(-1) - 1, axis=0) * 100
    F10, F20 = fwd(10), fwd(20)

    sd = pd.Index([d for d in dates if d >= START])
    W = lambda df: df.reindex(index=sd, columns=close.columns)  # noqa: E731
    liquid = W((vol.rolling(20).mean() >= LIQ) & opn.shift(-1).notna())
    liquid = liquid & pd.DataFrame({c: t_open.shift(-1).reindex(sd).notna().values for c in close.columns}, index=sd)
    F10, F20 = W(F10), W(F20)
    half = sd[len(sd) // 2]
    first = pd.DataFrame({c: sd < half for c in close.columns}, index=sd)
    base20 = F20.where(liquid).stack().mean()
    base10 = F10.where(liquid).stack().mean()

    def stat(name, m):
        m = W(m.astype(float)).fillna(0).astype(bool) & liquid
        v20, v10 = F20.where(m).stack().dropna(), F10.where(m).stack().dropna()
        a = F20.where(m & first).stack().dropna()
        b = F20.where(m & ~first).stack().dropna()
        return {"條件": name, "樣本": len(v20), "10日": round(v10.mean(), 2), "20日": round(v20.mean(), 2),
                "比平均": round(v20.mean() - base20, 2), "20日勝率": round((v20 > 0).mean() * 100, 1),
                "前半": round(a.mean() - F20.where(liquid & first).stack().mean(), 2),
                "後半": round(b.mean() - F20.where(liquid & ~first).stack().mean(), 2)}

    def show(title, items):
        print(f"\n===== {title}（隔天開盤買；全部平均 10日 {base10:.2f}%、20日 {base20:.2f}%；比平均＝20日減全部平均，前半／後半也是）=====")
        print(pd.DataFrame([stat(n, m) for n, m in items]).set_index("條件").to_string())

    # ===== 籌碼：法人買賣趨勢（和分析卡 chip_trend 相同算法）=====
    for name, col in (("外資", "foreign_net_shares"), ("投信", "sitca_net_shares"), ("自營商", "dealers_net_shares")):
        s = pv(ins, col).reindex_like(close).fillna(0)
        l5, p5 = s.rolling(5).sum(), s.rolling(5).sum().shift(5)
        pos, neg = s > 0, s < 0
        buy_streak = pos.cumsum() - pos.cumsum().where(~pos).ffill().fillna(0)
        sell_streak = neg.cumsum() - neg.cumsum().where(~neg).ffill().fillna(0)
        show(f"{name}買賣趨勢", [
            ("由賣轉買", (l5 > 0) & (p5 <= 0)), ("買超加速", (l5 > 0) & (p5 > 0) & (l5 > p5)),
            ("買超減緩", (l5 > 0) & (p5 > 0) & (l5 <= p5)), ("由買轉賣", (l5 < 0) & (p5 >= 0)),
            ("賣超加速", (l5 < 0) & (p5 < 0) & (l5 < p5)), ("賣超減緩", (l5 < 0) & (p5 < 0) & (l5 >= p5)),
            ("連買 ≥ 3 天", buy_streak >= 3), ("連買 ≥ 5 天", buy_streak >= 5),
            ("連賣 ≥ 3 天", sell_streak >= 3), ("連賣 ≥ 5 天", sell_streak >= 5),
        ])

    # ===== 大戶持股近 4 週變化（只用評分日之前公布的週資料）=====
    h = pv(holder, "big_holder_ratio", index="week_date")
    h4 = h - h.shift(4)
    week_for_day = pd.Series(h.index, index=h.index)
    pick = pd.Series([week_for_day[week_for_day < d].iloc[-1] if (week_for_day < d).any() else None for d in sd], index=sd)
    h4d = h4.reindex(pick.values).set_axis(sd).reindex(columns=close.columns)
    show("大戶持股近 4 週變化", [
        ("增加 ≥ 1 個百分點", h4d >= 1), ("增加 0～1", (h4d > 0) & (h4d < 1)),
        ("減少 0～1", (h4d < 0) & (h4d > -1)), ("減少 ≥ 1 個百分點", h4d <= -1),
    ])

    # ===== 資金：融資、融券餘額變化 =====
    mb = pv(margin, "margin_balance").reindex_like(close)
    sbal = pv(margin, "short_balance").reindex_like(close)
    m5 = (mb / mb.shift(5) - 1).where(mb.shift(5) > 0) * 100
    m20 = (mb / mb.shift(20) - 1).where(mb.shift(20) > 0) * 100
    s5 = (sbal / sbal.shift(5) - 1).where(sbal.shift(5) > 0) * 100
    d = mb.diff()
    up, dn = d > 0, d < 0
    m_up_streak = up.cumsum() - up.cumsum().where(~up).ffill().fillna(0)
    m_dn_streak = dn.cumsum() - dn.cumsum().where(~dn).ffill().fillna(0)
    p20 = (close / close.shift(20) - 1) * 100
    ratio = (sbal / mb).where(mb > 0) * 100
    show("融資餘額變化", [
        ("融資 5 日增 ≥ 10%", m5 >= 10), ("融資 5 日增 0～10%", (m5 > 0) & (m5 < 10)),
        ("融資 5 日減 0～10%", (m5 < 0) & (m5 > -10)), ("融資 5 日減 ≥ 10%", m5 <= -10),
        ("融資 20 日增 ≥ 20%", m20 >= 20), ("融資 20 日減 ≥ 20%", m20 <= -20),
        ("融資連增 ≥ 5 天", m_up_streak >= 5), ("融資連減 ≥ 5 天", m_dn_streak >= 5),
        ("股價漲、融資增（20 日）", (p20 > 0) & (m20 > 0)), ("股價跌、融資增（20 日）", (p20 < 0) & (m20 > 0)),
        ("股價漲、融資減（20 日）", (p20 > 0) & (m20 < 0)), ("股價跌、融資減（20 日）", (p20 < 0) & (m20 < 0)),
        ("融券 5 日增 ≥ 20%", s5 >= 20), ("融券 5 日減 ≥ 20%", s5 <= -20),
        ("券資比 < 2%", ratio < 2), ("券資比 2～5%", (ratio >= 2) & (ratio < 5)),
        ("券資比 5～10%", (ratio >= 5) & (ratio < 10)), ("券資比 10～20%", (ratio >= 10) & (ratio < 20)),
        ("券資比 ≥ 20%", ratio >= 20),
    ])

    # ===== 營收：M 月營收從 M+1 月 11 日起才可用 =====
    rev = pv(revenue, "revenue", index="year_month")
    yoy = pv(revenue, "yoy_growth", index="year_month").reindex_like(rev)
    cum = pv(revenue, "cumulative_yoy_growth", index="year_month").reindex_like(rev)
    ly = rev / (1 + yoy / 100)
    yoy3 = (rev.rolling(3).sum() / ly.rolling(3).sum() - 1) * 100
    accel = yoy3 - yoy3.shift(3)
    rank_top = rev >= rev.rolling(12, min_periods=6).max()
    pos_yoy = yoy > 0
    streak = pos_yoy.cumsum() - pos_yoy.cumsum().where(~pos_yoy).ffill().fillna(0)
    avail = pd.Series({ym: S._revenue_available_from(ym) for ym in rev.index})

    def daily(monthly):
        last = pd.Series([avail[avail <= dd].index[-1] if (avail <= dd).any() else None for dd in sd], index=sd)
        return monthly.reindex(last.values).set_axis(sd).reindex(columns=close.columns)
    yoy_d, cum_d, y3_d, ac_d, top_d, st_d = (daily(x) for x in (yoy, cum, yoy3, accel, rank_top.astype(float), streak))
    show("月營收年增率（最新已公布月份）", [
        ("< −20%", yoy_d < -20), ("−20～0%", (yoy_d >= -20) & (yoy_d < 0)), ("0～20%", (yoy_d >= 0) & (yoy_d < 20)),
        ("20～50%", (yoy_d >= 20) & (yoy_d < 50)), ("≥ 50%", yoy_d >= 50),
    ])
    show("近 3 月年增率", [
        ("< −10%", y3_d < -10), ("−10～0%", (y3_d >= -10) & (y3_d < 0)), ("0～10%", (y3_d >= 0) & (y3_d < 10)),
        ("10～30%", (y3_d >= 10) & (y3_d < 30)), ("≥ 30%", y3_d >= 30),
        ("成長加速（比前 3 月高 ≥ 10 個百分點）", ac_d >= 10), ("成長放緩（比前 3 月低 ≥ 10 個百分點）", ac_d <= -10),
        ("年增 > 0 且加速", (y3_d > 0) & (ac_d > 0)), ("年增 > 0 但放緩", (y3_d > 0) & (ac_d < 0)),
    ])
    show("累計營收年增率", [
        ("< −10%", cum_d < -10), ("−10～0%", (cum_d >= -10) & (cum_d < 0)), ("0～20%", (cum_d >= 0) & (cum_d < 20)),
        ("≥ 20%", cum_d >= 20),
    ])
    show("營收新高與連續年增", [
        ("創近 12 月新高", top_d == 1), ("連續年增 ≥ 3 個月", st_d >= 3), ("連續年增 ≥ 6 個月", st_d >= 6),
        ("連續年增 ≥ 12 個月", st_d >= 12), ("最新月年減", pos_yoy.astype(float).pipe(daily) == 0),
    ])

    # ===== 葛蘭碧八大法則（還原權值後的收盤價，和網站相同參數）=====
    f = rule_frames(close)
    show("葛蘭碧八大法則", [(f"法則 {n} {RULES[n][0]}（{RULES[n][2]}）", f["hits"][n]) for n in sorted(f["hits"])])
    foreign = pv(ins, "foreign_net_shares").reindex_like(close).fillna(0)
    cf = confirm_frames(close, vol, foreign)["hits"]
    all3 = cf["macd"] & cf["volume"] & cf["foreign"]
    show("葛蘭碧買進法則＋三個確認條件（外資 5 日買超、MACD 柱 > 0、量比 > 1.5）",
         [(f"法則 {n}＋確認", f["hits"][n] & all3) for n in (1, 2, 3, 4)])


if __name__ == "__main__":
    main()
