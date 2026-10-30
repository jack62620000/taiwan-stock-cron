import os
import sys
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.etf import BUY_FEE, MIN_FEE, history_growth, project_future, simulate_savings
from utils.etf_view import etf_options, load_etf_list, load_price_range, load_savings
from utils.ui import MUTED, SERIES, render_metric_cards, style_sign

# ETF 頁的「存股試算」分頁（pages/6_ETF.py 用 runpy 執行這個檔案；登入、頁面設定、重新整理按鈕在 ETF 頁）
MAX_ETFS = 4
FEE_DISCOUNTS = {"牌價（0.1425%）": 1.0, "6 折": 0.6, "2.8 折": 0.28}
MAX_YEARS = 30   # 往後最多試算到幾年後
# 往後算時的股價假設：每年漲幅 %（None 是照各檔資料庫裡過去的年化漲幅）
GROWTH = {"每年漲 3%（保守）": 3.0, "每年漲 6%（中性）": 6.0, "每年漲 9%（樂觀）": 9.0, "照各檔過去的漲幅": None}

info = load_etf_list()
options = etf_options(info)
first_day, last_day = load_price_range()
if not options or first_day is None:
    st.info("還沒有 ETF 股價資料。")
    st.stop()
first_day, last_day = date.fromisoformat(str(first_day)[:10]), date.fromisoformat(str(last_day)[:10])

st.caption("假設從某一天開始存 ETF，用資料庫裡的真實收盤價與配息，算到今天會累積多少；"
           f"結束日期選在 {last_day} 之後就會往後推估（未來股價和配息用假設值）。最多同時比較 4 檔。")
far = date(last_day.year + MAX_YEARS, last_day.month, min(last_day.day, 28))
defaults = [o for o in options if o.split(" ", 1)[0] in ("0050", "0056", "00878")][:2] or options[:1]
picked = st.multiselect("選 ETF（最多 4 檔）", options, default=defaults, max_selections=MAX_ETFS, key="etf_sv_ids")
c1, c2, c3 = st.columns(3)
with c1:
    start = st.date_input("開始日期", value=max(first_day, date(last_day.year - 1, last_day.month, 1)),
                          min_value=first_day, max_value=far, key="etf_sv_start")
    end = st.date_input("結束日期", value=last_day, min_value=first_day, max_value=far, key="etf_sv_end",
                        help=f"可以選到 {MAX_YEARS} 年後；{last_day} 之後的股價、配息是推估的")
with c2:
    monthly = st.number_input("每月扣款（元）", min_value=0, max_value=1_000_000, value=10_000, step=1_000, key="etf_sv_monthly")
    day = st.selectbox("每月扣款日", [1, 6, 11, 16, 21, 26], index=1, format_func=lambda v: f"{v} 日", key="etf_sv_day")
with c3:
    lump = st.number_input("開始時單筆投入（元）", min_value=0, max_value=100_000_000, value=0, step=10_000, key="etf_sv_lump")
    reinvest = st.toggle("配息再投入", value=True, key="etf_sv_reinvest",
                         help="開啟：配息發放當天用收盤價買回同一檔；關閉：配息領現金放著（仍算在總資產裡）")
    discount = st.selectbox("手續費", list(FEE_DISCOUNTS), key="etf_sv_fee")

if not picked:
    st.info("請選至少一檔 ETF。")
    st.stop()
if start >= end:
    st.warning("結束日期要晚於開始日期。")
    st.stop()
if monthly <= 0 and lump <= 0:
    st.warning("每月扣款和單筆投入至少要填一個。")
    st.stop()

future = end > last_day
growth_pick = None
if future:
    growth_pick = st.selectbox(f"{last_day} 之後的股價假設", list(GROWTH), index=1, key="etf_sv_growth",
                               help="未來股價每年照這個漲幅變動；配息照最近 12 個月的殖利率和月份每年重複")

ids = tuple(p.split(" ", 1)[0] for p in picked)
names = dict(zip(info["etf_id"], info["etf_name"].fillna("")))
# 往後推估要用最近 12 個月的股價和配息，所以至少從最新一天往前 400 天開始讀
since = min(start, last_day - timedelta(days=400)) if future else start
prices, divs, events = load_savings(ids, since.isoformat())
fee = BUY_FEE * FEE_DISCOUNTS[discount]

results, curves, lumps = [], [], []
for etf_id in ids:
    p = prices[prices["etf_id"] == etf_id] if not prices.empty else prices
    dv = divs[divs["etf_id"] == etf_id] if not divs.empty else divs
    ev = events[events["etf_id"] == etf_id] if not events.empty else events
    if p.empty:
        results.append({"代號": etf_id, "名稱": names.get(etf_id, ""), "說明": "這段期間沒有股價"})
        continue
    growth = None
    if future:
        growth = GROWTH[growth_pick]
        if growth is None:
            hist = load_savings((etf_id,), first_day.isoformat())
            growth = history_growth(hist[0], hist[2])
            growth = 0.0 if growth is None else growth
        p, dv, _ = project_future(p, dv, end, growth)
    detail, s = simulate_savings(p, dv, ev, start, end, monthly=monthly, day=day, lump=lump, reinvest=reinvest,
                                 fee_rate=fee, min_fee=MIN_FEE)
    if s is None:
        results.append({"代號": etf_id, "名稱": names.get(etf_id, ""), "說明": "這段期間沒有扣款日"})
        continue
    label = f"{etf_id} {names.get(etf_id, '')}"
    results.append({"代號": etf_id, "名稱": names.get(etf_id, ""), "投入本金": s["invested"], "目前總資產": s["value"],
                    "損益": s["gain"], "總報酬%": s["return_pct"], "年化報酬%": s["xirr_pct"], "領到配息": s["dividends"],
                    "持有股數": s["units"], "平均成本": s["avg_cost"], "手續費": s["fees"], "扣款次數": s["buys"],
                    "說明": f"{s['start']}～{s['end']}" + (f"（{last_day} 之後每年漲 {growth:.1f}% 推估）" if future else "")})
    curves.append(detail.assign(ETF=label))
    # 同樣的總金額在第一天單筆買進，比較定期定額和單筆的差別
    if monthly > 0:
        _, ls = simulate_savings(p, dv, ev, start, end, lump=s["invested"], reinvest=reinvest, fee_rate=fee, min_fee=MIN_FEE)
        if ls:
            lumps.append({"代號": etf_id, "名稱": names.get(etf_id, ""), "單筆投入": ls["invested"],
                          "目前總資產": ls["value"], "總報酬%": ls["return_pct"], "年化報酬%": ls["xirr_pct"],
                          "比定期定額多賺": ls["value"] - s["value"]})

ok = [r for r in results if "投入本金" in r]
if ok:
    best = max(ok, key=lambda r: r["目前總資產"])
    render_metric_cards([
        ("投入本金", f"{best['投入本金']:,.0f} 元", f"{best['代號']}，扣款 {best['扣款次數']} 次", None),
        ("目前總資產", f"{best['目前總資產']:,.0f} 元", f"損益 {best['損益']:+,.0f} 元", best["損益"]),
        ("總報酬", f"{best['總報酬%']:+.2f}%", f"含配息 {best['領到配息']:,.0f} 元", best["總報酬%"]),
        ("年化報酬", f"{best['年化報酬%']:+.2f}%" if best["年化報酬%"] is not None else "—", "考慮每筆錢投入的時間",
         best["年化報酬%"]),
    ])
    if len(ok) > 1:
        st.caption(f"卡片是總資產最高的 {best['代號']} {best['名稱']}，全部的比較在下表。")

st.subheader("📋 試算結果")
res = pd.DataFrame(results)
money = [c for c in ["投入本金", "目前總資產", "損益", "領到配息", "手續費"] if c in res.columns]
pct = [c for c in ["總報酬%", "年化報酬%"] if c in res.columns]
fmt = res.style.format("{:,.0f}", subset=money, na_rep="—").format("{:+.2f}", subset=pct, na_rep="—")
if "持有股數" in res.columns:
    fmt = fmt.format("{:,.0f}", subset=["持有股數", "扣款次數"], na_rep="—").format("{:,.2f}", subset=["平均成本"], na_rep="—")
st.dataframe(fmt.map(style_sign, subset=[c for c in ["損益", *pct] if c in res.columns]), hide_index=True, width="stretch")

if curves:
    st.subheader("📈 總資產走勢")
    cv = pd.concat(curves, ignore_index=True)
    lines = alt.Chart(cv).mark_line().encode(
        x=alt.X("date:T", title=None), y=alt.Y("value:Q", title="元"),
        color=alt.Color("ETF:N", scale=alt.Scale(range=SERIES), legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("date:T", title="日期"), "ETF", alt.Tooltip("value:Q", title="總資產", format=",.0f"),
                 alt.Tooltip("invested:Q", title="投入本金", format=",.0f"),
                 alt.Tooltip("dividends:Q", title="累計配息", format=",.0f")])
    base = alt.Chart(cv[cv["ETF"] == cv["ETF"].iloc[0]]).mark_line(color=MUTED, strokeDash=[4, 3]).encode(
        x="date:T", y="invested:Q", tooltip=[alt.Tooltip("invested:Q", title="投入本金", format=",.0f")])
    chart = lines + base
    if future and start <= last_day:
        cut = pd.DataFrame({"date": [pd.Timestamp(last_day)]})
        chart = chart + alt.Chart(cut).mark_rule(color=MUTED, strokeDash=[2, 2]).encode(x="date:T")
    st.altair_chart(chart, width="stretch")
    st.caption("彩色線是總資產（股票市值＋手上現金＋已除息還沒發放的配息），灰色虛線是累計投入的本金。"
               + (f"直的虛線是 {last_day}，右邊是用假設漲幅推估的，不是真的股價。" if future else ""))

if lumps:
    st.subheader("⚖️ 如果同樣的錢在第一天單筆買進")
    lp = pd.DataFrame(lumps)
    st.dataframe(lp.style.format("{:,.0f}", subset=["單筆投入", "目前總資產", "比定期定額多賺"])
                 .format("{:+.2f}", subset=["總報酬%", "年化報酬%"], na_rep="—")
                 .map(style_sign, subset=["比定期定額多賺", "總報酬%", "年化報酬%"]), hide_index=True, width="stretch")
    st.caption("一路上漲的期間單筆通常贏，先跌後漲的期間定期定額通常贏；定期定額的好處是不用挑進場時間、分散買在不同價位。")

with st.expander("📖 試算規則"):
    st.markdown(f"""
- **扣款**：每月 {day} 日用當天收盤價買進，遇到假日順延到下一個交易日；買整數股（零股），買不滿一股的零錢留到下個月。
- **手續費**：{discount}，每筆最低 {MIN_FEE} 元；沒有計算賣出的手續費和證交稅（還沒賣）。
- **配息**：除息前一天持有的股數 × 每單位配息，在發放日入帳（沒有發放日就用除息日）。
  開啟「配息再投入」時，發放當天用收盤價買回同一檔；關閉時配息留在手上，仍算進總資產。
  沒有扣二代健保補充保費（單次配息超過 2 萬元要扣 2.11%）和所得稅。
- **分割、反分割**：持有股數依比例自動調整。
- **年化報酬**：考慮每一筆錢投入的時間（內部報酬率 XIRR）；期間太短時會被放大，參考就好。
- 股價資料從 {first_day} 開始，最早只能從這天試算。
- **往後推估**（結束日期在 {last_day} 之後）：未來每個工作日的收盤價 = 最新收盤 ×（1＋每年漲幅）^ 經過年數；
  配息照最近 12 個月每次配息的殖利率（配息 ÷ 除息前收盤）和月份每年重複，已公告的配息照公告金額。
  「照各檔過去的漲幅」是資料庫裡最早到最新收盤的年化漲幅（不含配息），過去幾年漲很多的話會推估得很樂觀。
  推估只是假設，實際股價和配息一定會不同。
""")
