import streamlit as st
import pandas as pd
import altair as alt
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.db import get_stock_list, get_recent_closes
from utils.granville import RULES, STABLE_RULES, granville_signals, simulate_trades
from utils.ui import require_login, style_sign, UP_COLOR, DOWN_COLOR, WARN_COLOR
require_login()

st.set_page_config(
    page_title="葛蘭碧八大法則",
    page_icon="📐",
    layout="wide",
    initial_sidebar_state="expanded"
)
st.title("📐 葛蘭碧八大法則選股")

if st.button("🔄 強制重新整理資料"):
    st.cache_data.clear()
    get_stock_list.cache_clear()
    st.rerun()

# ===== 載入資料 =====
@st.cache_data(ttl=3600)
def load_prices():
    # 100 個日曆天約 65 個交易日，夠算季線（60 日均線）給法則 2 用
    with st.spinner("正在載入近 100 天股價..."):
        return get_recent_closes(calendar_days=100, columns="stock_id, date, close_price, volume")

stock_list = get_stock_list()
prices = load_prices()

if prices.empty:
    st.warning("查無股價資料")
    st.stop()

latest_date = prices["date"].max()

# ===== 側邊參數 =====
st.sidebar.header("⚙️ 判斷參數")
ma_days = st.sidebar.selectbox("均線", [20, 10], format_func=lambda d: f"{d} 日均線")
slope_days = st.sidebar.number_input("均線方向：與幾天前的均線比較", 2, 10, 5)
lookback = st.sidebar.number_input("法則 2、3、6、7：往回看幾天", 2, 10, 5)
near_pct = st.sidebar.number_input("法則 3、7：靠近均線的距離（%）", 0.5, 10.0, 2.0, step=0.5)
bias_pct = st.sidebar.number_input("法則 4、8：乖離過大門檻（%）", 3.0, 50.0, 10.0, step=1.0)

st.sidebar.header("🔍 篩選條件")
search_id = st.sidebar.text_input("股票代號（可輸入多個，用逗號分隔）", placeholder="例如: 2330,2454")
markets = ["全部"] + sorted(stock_list["market"].dropna().unique().tolist())
selected_market = st.sidebar.selectbox("市場", markets)
industries = ["全部"] + sorted(stock_list["industry"].dropna().unique().tolist())
selected_industry = st.sidebar.selectbox("產業別", industries)
min_volume = st.sidebar.number_input(
    "20 日均量下限（張）", 0, 1000000, 500, step=100,
    help="回測顯示冷門股的訊號雜訊很大，預設只看 20 日均量 500 張以上的股票",
)

# ===== 計算訊號 =====
signals = granville_signals(
    prices, ma_days=ma_days, slope_days=slope_days, lookback=lookback,
    near_pct=near_pct, bias_pct=bias_pct,
)

volume = prices[prices["date"] == latest_date][["stock_id", "volume"]] \
    .drop_duplicates(subset="stock_id")
volume["volume"] = pd.to_numeric(volume["volume"], errors="coerce") / 1000  # 股 → 張

df = signals.merge(stock_list[["stock_id", "stock_name", "market", "industry"]], on="stock_id", how="inner") \
    .merge(volume, on="stock_id", how="left")

if search_id:
    ids = [s.strip() for s in search_id.split(",") if s.strip()]
    df = df[df["stock_id"].isin(ids)]
if selected_market != "全部":
    df = df[df["market"] == selected_market]
if selected_industry != "全部":
    df = df[df["industry"] == selected_industry]
if min_volume > 0:
    df = df[df["avg_volume_20"] >= min_volume]

tab_today, tab_sim = st.tabs(["📋 今日訊號", "🧪 模擬交易"])

with tab_today:
    # ===== 各法則檔數 =====
    counts = {n: int(df["rules"].apply(lambda r: n in r).sum()) for n in RULES}

    st.caption(f"資料日期：{latest_date}　·　依 {ma_days} 日均線判斷，共 {len(df):,} 檔有足夠資料")

    buy_cols = st.columns(4)
    sell_cols = st.columns(4)
    for n, (side, desc, level) in RULES.items():
        col = buy_cols[n - 1] if n <= 4 else sell_cols[n - 5]
        col.metric(f"法則{n}（{side}·{level}）", f"{counts[n]:,} 檔", help=desc)

    with st.expander("📖 八大法則說明與判斷方式"):
        st.markdown("\n".join(f"- **法則{n}（{side}·{level}）**：{desc}" for n, (side, desc, level) in RULES.items()))
        st.markdown(f"""
**程式怎麼判斷（以最新一個交易日為準，參數可在左側調整）**
- 均線上揚／下彎：今天的均線比 {slope_days} 天前高／低。
- 法則 1、5：今天剛突破／跌破均線，而且 {slope_days} 天前的均線方向是相反或走平的（代表均線正在轉折）。
- 法則 2、6：均線方向一直沒變，過去 {lookback} 天內曾經跌破／突破均線，今天又回到原本那一側；法則 2 另外要求均線在季線（60 日均線）之上。
- 法則 3、7：過去 {lookback} 天都在均線同一側，最接近均線時乖離在 {near_pct}% 以內，今天往遠離均線的方向走。
- 法則 4、8：乖離率（收盤價與均線的差距）超過 ±{bias_pct}%。

**可靠度怎麼來的**
用 2025-09 ~ 2026-10 的資料回測（20 日均量 500 張以上、持有 10 個交易日，和同一天所有股票平均比較），
分成前後兩段期間：兩段方向一致的標「穩定」，兩段結果相反或樣本太少的標「參考」。
- 法則 2、6、7 兩段都和預期方向一致。
- 法則 8 原本是賣訊，但兩段期間都是續漲，乖離越大漲越多，所以改成「過熱」提醒。
- 法則 1、3、4、5 兩段結果不一致，預設不顯示，可在下方自行勾選。
- 所有法則的報酬中位數都略低於大盤，平均值是靠少數大漲股撐起來，單看訊號勝率不會超過五成。
""")
        st.caption("技術分析僅供參考，請搭配其他指標與基本面判斷。")

    st.markdown("---")

    # ===== 結果表 =====
    rule_options = list(RULES)
    selected_rules = st.multiselect(
        "顯示符合這些法則的股票",
        rule_options,
        default=STABLE_RULES,
        format_func=lambda n: f"法則{n}（{RULES[n][0]}·{RULES[n][2]}）",
    )

    result = df[df["rules"].apply(lambda r: any(n in selected_rules for n in r))].copy()

    def signal_of(rules):
        sides = {RULES[n][0] for n in rules}
        return "、".join(s for s in ["買進", "賣出", "過熱"] if s in sides)

    result["signal"] = result["rules"].apply(signal_of)
    result["rule_text"] = result["rules"].apply(lambda r: "、".join(f"法則{n}" for n in r))
    result["first_rule"] = result["rules"].str[0]  # 依法則編號、股票代號排序
    result = result.sort_values(["first_rule", "stock_id"]).reset_index(drop=True)

    st.subheader(f"📋 符合條件的股票（{len(result):,} 檔）")

    if result.empty:
        st.info("目前沒有股票符合選取的法則")
    else:

        col_map = {
            "stock_id":    "股票代號",
            "stock_name":  "公司名稱",
            "market":      "市場",
            "industry":    "產業別",
            "signal":      "訊號",
            "rule_text":   "符合法則",
            "close_price": "收盤價",
            "change":      "漲跌",
            "ma":          f"{ma_days}日均線",
            "bias":        "乖離率%",
            "ma_trend":    "均線方向",
            "volume":      "成交量(張)",
            "avg_volume_20": "20日均量(張)",
        }
        display_df = result[list(col_map)].rename(columns=col_map)

        def style_signal(val):
            if val == "買進":
                return f"color:{UP_COLOR};font-weight:700;"
            if val == "賣出":
                return f"color:{DOWN_COLOR};font-weight:700;"
            if val == "過熱":
                return f"color:{WARN_COLOR};font-weight:700;"
            return ""

        def style_trend(val):
            return style_sign({"上揚": 1, "下彎": -1}.get(val, 0))

        change = result["change"]
        styler = display_df.style \
            .format("{:,.2f}", subset=["收盤價", "漲跌", f"{ma_days}日均線", "乖離率%"], na_rep="") \
            .format("{:,.0f}", subset=["成交量(張)", "20日均量(張)"], na_rep="") \
            .map(style_sign, subset=["漲跌", "乖離率%"]) \
            .map(style_signal, subset=["訊號"]) \
            .map(style_trend, subset=["均線方向"]) \
            .apply(lambda s: [style_sign(c) for c in change], subset=["收盤價"], axis=0)

        st.dataframe(
            styler,
            hide_index=True,
            width="stretch",
            height=600,
            column_config={
                "股票代號": st.column_config.TextColumn(pinned=True),
                "公司名稱": st.column_config.TextColumn(pinned=True),
            },
        )

        # ===== 下載按鈕 =====
        csv = display_df.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            label="📥 下載篩選結果 CSV",
            data=csv,
            file_name=f"葛蘭碧八大法則_{latest_date}.csv",
            mime="text/csv",
        )


# =====================================================================
# 模擬交易：用歷史資料假設照著法則買賣，看結果如何
# =====================================================================
SIM_PERIODS = {"近 3 個月": 90, "近 6 個月": 180, "近 1 年": 365}
WARMUP_DAYS = 100  # 回測起點之前多載入的日曆天，夠算季線


@st.cache_data(ttl=3600)
def load_sim_prices(calendar_days):
    with st.spinner(f"正在載入近 {calendar_days} 天股價（含開盤價），第一次需要一點時間..."):
        return get_recent_closes(
            calendar_days=calendar_days,
            columns="stock_id, date, open_price, close_price, volume",
        )


@st.cache_data(ttl=3600)
def run_simulation(period_days, buy_rules, sell_rules, hold_days, stop_loss_pct, min_vol, params):
    sim_prices = load_sim_prices(period_days + WARMUP_DAYS)
    if sim_prices.empty:
        return pd.DataFrame(), None
    start = (pd.to_datetime(sim_prices["date"].max()) - pd.Timedelta(days=period_days)).strftime("%Y-%m-%d")
    return simulate_trades(
        sim_prices, list(buy_rules), list(sell_rules), hold_days=hold_days,
        stop_loss_pct=stop_loss_pct or None, min_volume=min_vol, start_date=start, **dict(params),
    )


with tab_sim:
    st.markdown(
        "用過去的股價假設「照著法則買賣」，看看會賺還是賠。"
        "收盤出現買進訊號 → **隔天開盤買進**；出現賣出訊號或跌破停損 → **隔天開盤賣出**；"
        "持有滿設定天數 → 當天收盤賣出。每檔股票同時只持有一筆，報酬已扣手續費與證交稅。"
    )
    s1, s2, s3 = st.columns(3)
    with s1:
        sim_period = st.selectbox("回測期間", list(SIM_PERIODS), index=1)
        sim_buy = st.multiselect(
            "買進法則", [1, 2, 3, 4], default=[2],
            format_func=lambda n: f"法則{n}（{RULES[n][2]}）",
        )
    with s2:
        sim_sell = st.multiselect(
            "賣出法則（出現就出場）", [5, 6, 7, 8], default=[6, 7],
            format_func=lambda n: f"法則{n}（{RULES[n][0]}·{RULES[n][2]}）",
        )
        sim_hold = st.number_input("最長持有天數（交易日）", 1, 120, 20)
    with s3:
        sim_stop = st.number_input("停損（%，0 表示不停損）", 0.0, 50.0, 8.0, step=1.0)
        sim_amount = st.number_input("每筆投入金額（元）", 10000, 10000000, 100000, step=10000)
    st.caption(
        f"均線參數與左側相同（{ma_days} 日均線），只買 20 日均量 ≥ {min_volume:,} 張的股票；"
        "市場、產業、代號篩選不套用在模擬上。"
    )

    if st.button("▶️ 開始模擬", type="primary"):
        st.session_state["granville_sim"] = True

    if not sim_buy:
        st.info("請至少選一條買進法則")
    elif st.session_state.get("granville_sim"):
        params = (("ma_days", ma_days), ("slope_days", slope_days), ("lookback", lookback),
                  ("near_pct", near_pct), ("bias_pct", bias_pct))
        trades, _ = run_simulation(
            SIM_PERIODS[sim_period], tuple(sorted(sim_buy)), tuple(sorted(sim_sell)),
            int(sim_hold), float(sim_stop), int(min_volume), params,
        )
        if trades.empty:
            st.info("這段期間沒有符合條件的交易")
        else:
            closed = trades[trades["exit_reason"] != "未平倉"]
            ret = trades["return_pct"]
            excess = ret - trades["benchmark_pct"]
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("交易筆數", f"{len(trades):,}", f"未平倉 {len(trades) - len(closed):,} 筆", delta_color="off")
            m2.metric("勝率（扣成本後賺錢）", f"{(ret > 0).mean():.0%}")
            m3.metric("平均每筆報酬", f"{ret.mean():+.2f}%", f"中位數 {ret.median():+.2f}%", delta_color="off")
            m4.metric("平均超額報酬（vs 同期大盤）", f"{excess.mean():+.2f}%",
                      f"贏大盤 {(excess > 0).mean():.0%} 筆", delta_color="off")
            m5, m6, m7, m8 = st.columns(4)
            pnl = ret / 100 * sim_amount
            m5.metric("累積損益（全部）", f"{pnl.sum():+,.0f} 元")
            m6.metric("平均持有天數", f"{trades['hold_days'].mean():.1f} 天")
            m7.metric("最大單筆獲利", f"{ret.max():+.1f}%")
            m8.metric("最大單筆虧損", f"{ret.min():+.1f}%")

            # 已實現損益累積曲線（依出場日）
            curve = closed.assign(pnl=closed["return_pct"] / 100 * sim_amount) \
                .groupby("exit_date")["pnl"].sum().cumsum().reset_index()
            curve["exit_date"] = pd.to_datetime(curve["exit_date"])
            if not curve.empty:
                st.altair_chart(
                    alt.Chart(curve).mark_line().encode(
                        x=alt.X("exit_date:T", title="出場日", axis=alt.Axis(format="%m/%d")),
                        y=alt.Y("pnl:Q", title="已實現累積損益（元）"),
                        tooltip=[alt.Tooltip("exit_date:T", title="出場日"),
                                 alt.Tooltip("pnl:Q", title="累積損益", format=",.0f")],
                    ).properties(height=300),
                    width="stretch",
                )

            # 出場原因統計
            by_reason = trades.groupby("exit_reason").agg(
                筆數=("return_pct", "size"),
                勝率=("return_pct", lambda r: (r > 0).mean()),
                平均報酬=("return_pct", "mean"),
            ).reset_index().rename(columns={"exit_reason": "出場原因"})
            st.markdown("**出場原因統計**")
            st.dataframe(
                by_reason.style.format({"勝率": "{:.0%}", "平均報酬": "{:+.2f}%"})
                    .map(style_sign, subset=["平均報酬"]),
                hide_index=True,
            )

            # 交易明細
            detail = trades.merge(stock_list[["stock_id", "stock_name"]], on="stock_id", how="left")
            detail["rule_text"] = detail["rules"].apply(lambda r: "、".join(f"法則{n}" for n in r))
            detail["pnl"] = detail["return_pct"] / 100 * sim_amount
            detail = detail.sort_values("entry_date", ascending=False)
            sim_cols = {
                "stock_id": "股票代號", "stock_name": "公司名稱", "rule_text": "進場法則",
                "entry_date": "買進日", "entry_price": "買進價", "exit_date": "賣出日",
                "exit_price": "賣出價", "hold_days": "持有天數", "exit_reason": "出場原因",
                "return_pct": "報酬%", "benchmark_pct": "同期大盤%", "pnl": "損益(元)",
            }
            detail_df = detail[list(sim_cols)].rename(columns=sim_cols)
            st.markdown(f"**交易明細（{len(detail_df):,} 筆）**")
            st.dataframe(
                detail_df.style
                    .format("{:,.2f}", subset=["買進價", "賣出價"])
                    .format("{:+.2f}", subset=["報酬%", "同期大盤%"])
                    .format("{:+,.0f}", subset=["損益(元)"])
                    .map(style_sign, subset=["報酬%", "同期大盤%", "損益(元)"]),
                hide_index=True,
                width="stretch",
                height=500,
                column_config={
                    "股票代號": st.column_config.TextColumn(pinned=True),
                    "公司名稱": st.column_config.TextColumn(pinned=True),
                },
            )
            st.download_button(
                label="📥 下載交易明細 CSV",
                data=detail_df.to_csv(index=False, encoding="utf-8-sig"),
                file_name=f"葛蘭碧模擬交易_{latest_date}.csv",
                mime="text/csv",
            )
            st.caption(
                "⚠️ 模擬限制：股價未還原除權息；手續費以牌價 0.1425% 計（未打折）；"
                "假設開盤一定買得到、賣得掉（漲跌停鎖死時實際上可能成交不了）；"
                "同期大盤是全部股票的等權平均漲跌。過去結果不代表未來表現。"
            )
    else:
        st.info("設定好條件後，按「開始模擬」。")

