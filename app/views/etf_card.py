import os
import sys

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.etf import PREMIUM_WARN, adjust, fill_label, fill_status, total_return, trailing_dividends
from utils.etf_view import CARD_KEY, etf_options, load_card, load_etf_list, style_premium
from utils.indicators import calc_all
from utils.kline import PERIODS, _line, align_to_bars, build_charts, render_html, resample_bars, resample_ins
from utils.ui import ACCENT, GLOW, MUTED, UP_COLOR, DOWN_COLOR, WARN_COLOR, render_metric_cards, style_sign

# ETF 頁的「ETF 分析卡」分頁（pages/6_ETF.py 用 runpy 執行這個檔案；登入、頁面設定、重新整理按鈕在 ETF 頁）
INDEX_COLOR = "#a78bfa"
TW_HOLDINGS = ("國內上市", "國內上櫃")
RANGE_CALENDAR_DAYS = {120: 182, 250: 365, 750: 1096}   # 顯示區間（交易日）換成日曆天，週線、月線用


def _n(s):
    return pd.to_numeric(s, errors="coerce")


def fmt_big(v, unit="元"):
    """金額、人數縮寫成「億」「萬」"""
    if v is None or pd.isna(v):
        return "—"
    if abs(v) >= 1e8:
        return f"{v / 1e8:,.1f} 億{'' if unit == '元' else unit}"
    if abs(v) >= 1e4:
        return f"{v / 1e4:,.1f} 萬{'' if unit == '元' else unit}"
    return f"{v:,.0f} {unit}"


# ===== 選 ETF =====
info = load_etf_list()
options = etf_options(info)
if not options:
    st.info("還沒有 ETF 清單。")
    st.stop()
want = st.session_state.pop(CARD_KEY, None)
if want or "etf_card_select" not in st.session_state:
    want = want or st.session_state.get("etf_card_last", "0050")
    st.session_state["etf_card_select"] = next((o for o in options if o.startswith(f"{want} ")), options[0])
selected = st.selectbox("搜尋 ETF（輸入代號或名稱）", options, key="etf_card_select", placeholder="例如 0050 或 高股息")
if not selected:
    st.stop()
etf_id = selected.split(" ", 1)[0]
recent = [s for s in st.session_state.get("etf_card_recent", []) if s != etf_id]
st.session_state["etf_card_recent"] = [etf_id] + recent[:5]
st.session_state["etf_card_last"] = etf_id
if recent:
    names = dict(zip(info["etf_id"], info["etf_name"].fillna("")))
    labels = {f"{s} {names.get(s, '')}".strip(): s for s in recent[:5]}
    picked = st.pills("最近查看", list(labels), key=f"etf_recent_pick_{etf_id}")
    if picked:
        st.session_state[CARD_KEY] = labels[picked]
        st.rerun()

d = load_card(etf_id)
meta, prices = d["meta"], d["prices"]
if prices.empty:
    st.info(f"{etf_id} 還沒有股價資料。")
    st.stop()

# ===== 標題 =====
tags = [t for t in (meta.get("category"), meta.get("dividend_frequency"), meta.get("market")) if t]
st.markdown(f"### {etf_id} {meta.get('etf_name', '')}　" + "　".join(f"`{t}`" for t in tags))
sub = [meta.get("issuer"), f"追蹤 {meta['index_name']}" if meta.get("index_name") else None,
       f"{meta['listing_date']} 上市" if meta.get("listing_date") else None]
st.caption("・".join(s for s in sub if s) or " ")

# ===== 重點卡片 =====
px = prices.sort_values("date").reset_index(drop=True)
close = _n(px["close_price"])
traded = px[close.notna()]
last = traded.iloc[-1]
last_close, chg = float(last["close_price"]), _n(pd.Series([last.get("change")])).iloc[0]
prev_close = last_close - chg if pd.notna(chg) else None
events = d["events"]
latest_date = str(last["date"])
year_cut = (pd.Timestamp(latest_date) - pd.Timedelta(days=365)).strftime("%Y-%m-%d")
year_row = traded[traded["date"].astype(str) <= year_cut]
ret_1y = None
if not year_row.empty:
    ret_1y = total_return(pd.Series({etf_id: last_close}), pd.Series({etf_id: year_row.iloc[-1]["close_price"]}),
                          events, year_row.iloc[-1]["date"], latest_date).get(etf_id)
idx_ret = None
if d["index_name"] and not year_row.empty:
    idx = d["index"].assign(date=lambda x: x["date"].astype(str))
    a, b = idx[idx["date"] <= str(year_row.iloc[-1]["date"])], idx[idx["date"] <= latest_date]
    if not a.empty and not b.empty:
        idx_ret = (_n(b["close"]).iloc[-1] / _n(a["close"]).iloc[-1] - 1) * 100
divs12 = trailing_dividends(d["dividends"], latest_date).get(etf_id)
nav = d["nav"].sort_values("date") if not d["nav"].empty else d["nav"]
stats = d["stats"].sort_values("year_month") if not d["stats"].empty else d["stats"]
exp = d["expense"]
exp_year = exp[exp["period_type"] == "year"].sort_values("period") if not exp.empty else exp

cards = [("收盤", f"{last_close:,.2f}",
          f"{chg:+,.2f}（{chg / prev_close * 100:+.2f}%）" if prev_close else latest_date, chg if pd.notna(chg) else 0)]
if not nav.empty:
    n = nav.iloc[-1]
    prem = _n(pd.Series([n["premium_pct"]])).iloc[0]
    cards.append(("折溢價", f"{prem:+.2f}%" if pd.notna(prem) else "—",
                  f"預估淨值 {float(n['est_nav']):,.2f}" if pd.notna(n.get("est_nav")) else "", prem))
else:
    cards.append(("折溢價", "—", "還沒有淨值資料", None))
if not stats.empty:
    s = stats.iloc[-1]
    p = stats.iloc[-2] if len(stats) > 1 else None
    aum, holders = _n(pd.Series([s["aum"]])).iloc[0], _n(pd.Series([s["holders_total"]])).iloc[0]
    d_aum = aum - float(p["aum"]) if p is not None and pd.notna(p["aum"]) and pd.notna(aum) else None
    d_hold = holders - float(p["holders_total"]) if p is not None and pd.notna(p["holders_total"]) and pd.notna(holders) else None
    cards += [(f"規模（{s['year_month']}）", fmt_big(aum), f"月增 {'+' if d_aum > 0 else ''}{fmt_big(d_aum)}" if d_aum is not None else "", d_aum),
              ("受益人數", fmt_big(holders, "人"), f"月增 {d_hold:+,.0f} 人" if d_hold is not None else "", d_hold)]
cards.append(("近 12 月殖利率", f"{divs12 / last_close * 100:.2f}%" if divs12 else "—",
              f"配息合計 {divs12:,.3f} 元" if divs12 else "近一年沒有配息", None))
if not exp_year.empty:
    e = exp_year.iloc[-1]
    cards.append((f"{e['period']} 年費用率", f"{float(e['total_pct']):.2f}%",
                  f"經理費 {float(e['mgmt_pct']):.2f}%" if pd.notna(e.get("mgmt_pct")) else "", None))
cards.append(("近 1 年含息報酬", f"{ret_1y:+.2f}%" if ret_1y is not None and pd.notna(ret_1y) else "—",
              f"追蹤指數 {idx_ret:+.2f}%" if idx_ret is not None else "", ret_1y))
render_metric_cards(cards)

# ===== K 線 =====
st.subheader("📈 股價走勢（還原權值）" + ("＋追蹤指數" if d["index_name"] else ""))
c0, c1 = st.columns([2, 3])
with c0:
    period = st.radio("K 線", list(PERIODS), horizontal=True, key="etf_kline_period")
with c1:
    range_days = st.radio("顯示區間", [120, 250, 750], index=1, horizontal=True, key="etf_kline_range",
                          format_func=lambda v: {120: "近半年", 250: "近 1 年", 750: "近 3 年"}[v])
adj = adjust(px, events)
ins = d["ins"]
if PERIODS[period] is None:
    bars = calc_all(adj)
    charts = build_charts(bars, range_days, overlays=("均線",), sub="外資買賣超", ins=ins, markers=False, events=events)
    shown = bars.tail(range_days)
else:
    code = PERIODS[period]
    bars = calc_all(resample_bars(adj, code))   # 均線用全部歷史算，再只畫顯示區間內的 K 棒
    cut = (pd.Timestamp(bars["date"].iloc[-1]) - pd.Timedelta(days=RANGE_CALENDAR_DAYS[range_days])).strftime("%Y-%m-%d")
    n_bars = max(int((bars["date"].astype(str) > cut).sum()), 2)
    charts = build_charts(bars, n_bars, overlays=("均線",), sub="外資買賣超", ins=resample_ins(ins, bars, code),
                          markers=False, events=align_to_bars(events, bars, code))
    shown = bars.tail(n_bars)
# 追蹤指數換算成和 ETF 同一個起點，疊在主圖上（只有日線）
if d["index_name"] and PERIODS[period] is None and not shown.empty:
    idx = d["index"].assign(date=lambda x: x["date"].astype(str))
    m = shown.assign(date=shown["date"].astype(str))[["date", "close_price"]].merge(idx, on="date", how="inner")
    m = m.dropna()
    if len(m) > 1:
        m["time"] = m["date"]
        m["idx"] = _n(m["close"]) / _n(m["close"]).iloc[0] * float(m["close_price"].iloc[0])
        charts[0]["series"].append(_line(m, "idx", INDEX_COLOR, "追蹤指數", 2, 2))
kline_html, kline_height = render_html(charts)
components.html(kline_html, height=kline_height)
if PERIODS[period] is not None:
    unit = "週" if period == "週線" else "個月"
    st.caption(f"{period}：每根 K 棒是一{unit}，共 {n_bars} 根；MA5／MA20／MA60 是 5、20、60 根 K 棒的平均。")
st.caption(
    "股價已還原權值：除息、分割之前的價格依參考價等比例調整，看得出含息的實際漲幅；成交量上方橘色方塊是除息或分割的日子。"
    "下方是外資買賣超（張）。"
    + (f"紫色虛線是追蹤指數「{d['index_name']}」，換算成和 ETF 同一個起點，兩條線差距越大代表追蹤誤差越大。"
       if d["index_name"] else "")
)

# ===== 資金流向、人氣 =====
if not stats.empty:
    st.subheader("💹 資金流向與人氣（投信投顧公會月資料）")
    m = stats.copy()
    m["申購(億)"] = _n(m["subscription_amount"]) / 1e8
    m["買回(億)"] = -_n(m["redemption_amount"]) / 1e8
    m["規模(億)"] = _n(m["aum"]) / 1e8
    m["受益人數(萬)"] = _n(m["holders_total"]) / 1e4
    m["定期定額戶數(萬)"] = _n(m["dca_accounts"]) / 1e4
    left, right = st.columns(2)
    with left:
        flow = m.melt("year_month", ["申購(億)", "買回(億)"], var_name="項目", value_name="億元")
        bars_c = alt.Chart(flow).mark_bar().encode(
            x=alt.X("year_month:N", title=None), y=alt.Y("億元:Q", title="億元"),
            color=alt.Color("項目:N", scale=alt.Scale(domain=["申購(億)", "買回(億)"], range=[UP_COLOR, DOWN_COLOR]),
                            legend=alt.Legend(orient="top", title=None)),
            tooltip=["year_month", "項目", alt.Tooltip("億元:Q", format=",.1f")])
        aum_line = alt.Chart(m).mark_line(color=ACCENT, point=True).encode(
            x="year_month:N", y=alt.Y("規模(億):Q", title="規模（億，金線）"),
            tooltip=["year_month", alt.Tooltip("規模(億):Q", format=",.0f")])
        st.altair_chart(alt.layer(bars_c, aum_line).resolve_scale(y="independent"), width="stretch")
        st.caption("紅色是每月申購金額、綠色是買回金額（往下），金線是月底規模。申購多於買回代表有新資金流入。")
    with right:
        ppl = m.melt("year_month", ["受益人數(萬)", "定期定額戶數(萬)"], var_name="項目", value_name="萬人")
        st.altair_chart(alt.Chart(ppl).mark_line(point=True).encode(
            x=alt.X("year_month:N", title=None), y=alt.Y("萬人:Q", title="萬人"),
            color=alt.Color("項目:N", scale=alt.Scale(range=[ACCENT, GLOW]), legend=alt.Legend(orient="top", title=None)),
            tooltip=["year_month", "項目", alt.Tooltip("萬人:Q", format=",.2f")]), width="stretch")
        st.caption("受益人數是持有這檔的人數；定期定額戶數是當月有扣款的戶數，越多代表越多人長期存。")
elif meta.get("category") == "期貨商品" or "期貨" in str(meta.get("fund_type") or ""):
    st.caption("期貨信託類 ETF 不在投信投顧公會的月資料內，沒有規模、受益人數。")

# ===== 前十大持股、配息 =====
left, right = st.columns(2)
with left:
    top = d["top"]
    if top.empty:
        st.subheader("🧺 前十大持股")
        st.info("沒有持股資料。")
    else:
        months = sorted(top["year_month"].unique())
        cur = top[top["year_month"] == months[-1]].sort_values("rank")
        prev = top[top["year_month"] == months[-2]] if len(months) > 1 else top.iloc[0:0]
        st.subheader(f"🧺 前十大持股（{months[-1]}）")
        t = cur[["rank", "holding_code", "holding_name", "holding_type", "weight_pct"]].copy()
        t["weight_pct"] = _n(t["weight_pct"])
        prev_w = dict(zip(prev["holding_name"], _n(prev["weight_pct"])))
        t["比上月"] = [w - prev_w[n] if n in prev_w else None for n, w in zip(t["holding_name"], t["weight_pct"])]
        t["新進"] = ["🆕" if len(prev) and n not in prev_w else "" for n in t["holding_name"]]
        t.columns = ["#", "代號", "名稱", "類型", "權重%", "比上月", "新進"]
        ev = st.dataframe(
            t.style.format("{:+.2f}", subset=["比上月"], na_rep="—").map(style_sign, subset=["比上月"]),
            hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
            key=f"etf_top_{etf_id}_{st.session_state.get('etf_top_n', 0)}",
            column_config={"權重%": st.column_config.ProgressColumn(
                format="%.2f%%", min_value=0, max_value=max(float(t["權重%"].max() or 0), 1.0))})
        st.caption(f"前十大合計 {t['權重%'].sum():.1f}%。「比上月」是權重變化（百分點），🆕 是這個月新進前十大。"
                   "點一列台股持股可以開它的個股分析卡。")
        rows = ev.selection.rows if ev and hasattr(ev, "selection") else []
        if rows:
            r = cur.iloc[rows[0]]
            st.session_state["etf_top_n"] = st.session_state.get("etf_top_n", 0) + 1
            if r["holding_type"] in TW_HOLDINGS and r["holding_code"]:
                st.session_state["card_stock_id"] = str(r["holding_code"])
                st.switch_page("pages/2_個股分析卡.py")
            else:
                st.toast(f"{r['holding_name']} 不是台股，沒有個股分析卡。")
with right:
    st.subheader("💵 配息紀錄")
    divs = d["dividends"]
    if divs.empty:
        st.info("沒有配息紀錄（可能不配息，或剛上市還沒配過）。")
    else:
        dv = fill_status(divs.sort_values("ex_date", ascending=False).head(12), px)
        dv["cash_dividend"] = _n(dv["cash_dividend"])
        dv["殖利率%"] = dv["cash_dividend"] / _n(dv["除息前收盤"]) * 100
        dv["pay_date"] = dv["pay_date"].fillna("—")
        dv["填息"] = [fill_label(f, n) for f, n in zip(dv["填息"], dv["填息天數"])]
        future = pd.to_datetime(dv["ex_date"]) > pd.Timestamp(latest_date)
        dv.loc[future, "填息"] = "📅 即將除息"
        out = dv[["ex_date", "pay_date", "cash_dividend", "殖利率%", "填息"]].rename(columns={
            "ex_date": "除息日", "pay_date": "發放日", "cash_dividend": "每單位(元)"})
        st.dataframe(out.style.format("{:.3f}", subset=["每單位(元)"], na_rep="待公告")
                     .format("{:.2f}", subset=["殖利率%"], na_rep="—"),
                     hide_index=True, width="stretch")
        st.caption("殖利率是單次配息 ÷ 除息前一天收盤；填息是除息後收盤漲回除息前一天收盤，天數是除息後第幾個交易日填息。")

# ===== 籌碼 =====
st.subheader("🏦 籌碼")


def lots_sum(df, col, days):
    if df.empty or col not in df.columns:
        return None
    v = _n(df.sort_values("date").tail(days)[col])
    return v.sum(min_count=1) / 1000


def streak(df, col):
    """最近連續買超（正）或賣超（負）幾天"""
    if df.empty:
        return 0
    v = _n(df.sort_values("date")[col]).dropna().tolist()[::-1]
    if not v or v[0] == 0:
        return 0
    sign = 1 if v[0] > 0 else -1
    n = 0
    for x in v:
        if x * sign > 0:
            n += 1
        else:
            break
    return n * sign


rows = []
for name, col in (("外資", "foreign_net_shares"), ("投信", "sitca_net_shares"), ("自營商", "dealers_net_shares")):
    s = streak(ins, col)
    rows.append({"項目": name, "近5日": lots_sum(ins, col, 5), "近20日": lots_sum(ins, col, 20),
                 "最新": None, "說明": (f"連買 {s} 天" if s > 0 else f"連賣 {-s} 天" if s < 0 else "")})
mg = d["margin"].sort_values("date") if not d["margin"].empty else d["margin"]
if not mg.empty:
    a, b = mg.iloc[-1], mg.iloc[-6] if len(mg) > 5 else mg.iloc[0]
    rows.append({"項目": "融資餘額", "近5日": _n(pd.Series([a["margin_balance"]])).iloc[0] - _n(pd.Series([b["margin_balance"]])).iloc[0],
                 "近20日": None, "最新": _n(pd.Series([a["margin_balance"]])).iloc[0],
                 "說明": f"使用率 {float(a['margin_rate']):.2f}%（{a['date']}）" if pd.notna(a.get("margin_rate")) else str(a["date"])})
    rows.append({"項目": "融券餘額", "近5日": _n(pd.Series([a["short_balance"]])).iloc[0] - _n(pd.Series([b["short_balance"]])).iloc[0],
                 "近20日": None, "最新": _n(pd.Series([a["short_balance"]])).iloc[0], "說明": ""})
sb = d["sbl"].sort_values("date") if not d["sbl"].empty else d["sbl"]
if not sb.empty:
    a, b = sb.iloc[-1], sb.iloc[-6] if len(sb) > 5 else sb.iloc[0]
    bal, bal0 = _n(pd.Series([a["sbl_balance"]])).iloc[0] / 1000, _n(pd.Series([b["sbl_balance"]])).iloc[0] / 1000
    rows.append({"項目": "借券賣出餘額", "近5日": bal - bal0, "近20日": None, "最新": bal,
                 "說明": "借券賣出多半是避險或看空，餘額增加代表放空的人變多"})
table = pd.DataFrame(rows)
table[["近5日", "近20日", "最新"]] = table[["近5日", "近20日", "最新"]].apply(_n)  # None 換成 NaN，表格才會顯示空白
st.dataframe(table.style.format("{:+,.0f}", subset=["近5日", "近20日"], na_rep="")
             .format("{:,.0f}", subset=["最新"], na_rep="").map(style_sign, subset=["近5日", "近20日"]),
             hide_index=True, width="stretch")
hd = d["holders"].sort_values("week_date") if not d["holders"].empty else d["holders"]
if not hd.empty:
    h, h0 = hd.iloc[-1], hd.iloc[-5] if len(hd) > 4 else hd.iloc[0]
    big, big0 = _n(pd.Series([h["big_holder_ratio"], h0["big_holder_ratio"]]))
    st.caption(f"集保（{h['week_date']}）：400 張以上大戶持有 {big:.2f}%（4 週前 {big0:.2f}%），"
               f"20 張以下散戶 {float(h['small_holder_ratio']):.2f}%，受益人 {float(h['total_holders']):,.0f} 人。"
               "單位：張；融資融券、借券是前一個交易日的資料。")

# ===== 折溢價、單位數 =====
st.subheader("⚖️ 折溢價與發行單位數")
if nav.empty:
    st.info("還沒有淨值資料。")
else:
    nv = nav.tail(20).copy()
    for c in ("market_price", "est_nav", "nav", "premium_pct"):
        nv[c] = _n(nv[c])
    nv["單位數(萬)"] = _n(nv["units"]) / 1e4
    nv["單位增減(萬)"] = _n(nv["units_change"]) / 1e4
    out = nv[["date", "market_price", "est_nav", "nav", "premium_pct", "單位數(萬)", "單位增減(萬)"]].rename(columns={
        "date": "日期", "market_price": "市價", "est_nav": "預估淨值", "nav": "正式淨值", "premium_pct": "折溢價%"})
    st.dataframe(out.iloc[::-1].style.format("{:,.2f}", subset=["市價", "預估淨值", "正式淨值"], na_rep="—")
                 .format("{:+.2f}", subset=["折溢價%"], na_rep="—").format("{:,.0f}", subset=["單位數(萬)"], na_rep="—")
                 .format("{:+,.0f}", subset=["單位增減(萬)"], na_rep="—")
                 .map(style_premium, subset=["折溢價%"]).map(style_sign, subset=["單位增減(萬)"]),
                 hide_index=True, width="stretch")
    st.caption(f"折溢價 = 市價相對預估淨值，正數是溢價（買貴了）、負數是折價；超過 ±{PREMIUM_WARN}% 標橘色。"
               "單位數增加代表有人申購（新資金流入），減少代表有人買回。淨值資料從 2026-10-28 開始每天累積。")

# ===== 費用率 =====
if not exp.empty:
    st.subheader("🧾 費用率")
    if not exp_year.empty:
        cols = {"mgmt_pct": "經理費", "custody_pct": "保管費", "brokerage_pct": "手續費", "tax_pct": "交易稅",
                "creation_pct": "申購買回費（扣）", "other_pct": "其他", "total_pct": "合計"}
        ft = exp_year.set_index("period")[list(cols)].rename(columns=cols).T
        ft = ft[ft.columns[::-1]].apply(_n)
        st.dataframe(ft.style.format("{:.2f}%", na_rep="—"), width="stretch")
        st.caption("投信投顧公會的各項費用比率（全年，%），合計 = 手續費＋交易稅－申購買回交易費＋經理費＋保管費＋保證費＋其他。"
                   "經理費、保管費是固定的內扣費用；手續費、交易稅是基金買賣成分股的成本，換股越多越高。")
    else:
        st.caption("還沒有完整年度的費用率。")
