import streamlit as st
import pandas as pd
import altair as alt
import sys
import os
import math

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.db import get_stock_list, get_recent_closes, get_recent_ins
from utils.granville import (
    RULES, STABLE_RULES, CONFIRMS, HOLD_HINT, WATCH_DAYS, WATCH_STATUS, LATE_STATUS,
    granville_signals, rule1_watchlist, rule_frames, simulate_trades, watch_note,
)
from utils.paper import add_form
from utils.ui import require_login, style_sign, UP_COLOR, DOWN_COLOR, WARN_COLOR, ACCENT, MUTED
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

@st.cache_data(ttl=3600)
def load_ins(calendar_days=60):
    # 60 個日曆天約 40 個交易日：夠算外資 5 日買超，也涵蓋觀察名單往回看的 30 個交易日
    return get_recent_ins(calendar_days=calendar_days)

stock_list = get_stock_list()
prices = load_prices()
try:
    ins = load_ins()
except Exception:
    ins = None
    st.warning("法人資料暫時讀取失敗，外資條件先不顯示。")

if prices.empty:
    st.warning("查無股價資料")
    st.stop()

latest_date = prices["date"].max()

# ===== 側邊參數 =====
# 回測可靠度是用這組預設值算的；用 session_state 給初始值，「恢復預設」按鈕才能把值改回來
PARAM_DEFAULTS = {
    "g_ma_days": 20, "g_slope_days": 5, "g_lookback": 5, "g_near_pct": 2.0, "g_bias_pct": 10.0,
    "g_min_volume": 500, **{f"confirm_{key}": False for key in CONFIRMS},
}
for key, value in PARAM_DEFAULTS.items():
    st.session_state.setdefault(key, value)


def reset_params():
    st.session_state.update(PARAM_DEFAULTS)


st.sidebar.header("⚙️ 判斷參數")
st.sidebar.button("↩️ 恢復預設參數", on_click=reset_params, width="stretch",
                  help="頁面上的回測可靠度是用預設參數算的，改過參數後可一鍵恢復")
ma_days = st.sidebar.selectbox("均線", [20, 10], format_func=lambda d: f"{d} 日均線", key="g_ma_days")
slope_days = st.sidebar.number_input("均線方向：與幾天前的均線比較", 2, 10, key="g_slope_days")
lookback = st.sidebar.number_input("法則 2、3、6、7：往回看幾天", 2, 10, key="g_lookback")
near_pct = st.sidebar.number_input("法則 3、7：靠近均線的距離（%）", 0.5, 10.0, step=0.5, key="g_near_pct")
bias_pct = st.sidebar.number_input("法則 4、8：乖離過大門檻（%）", 3.0, 50.0, step=1.0, key="g_bias_pct")

st.sidebar.header("🔍 篩選條件")
search_id = st.sidebar.text_input("股票代號（可輸入多個，用逗號分隔）", placeholder="例如: 2330,2454")
markets = ["全部"] + sorted(stock_list["market"].dropna().unique().tolist())
selected_market = st.sidebar.selectbox("市場", markets)
industries = ["全部"] + sorted(stock_list["industry"].dropna().unique().tolist())
selected_industry = st.sidebar.selectbox("產業別", industries)
min_volume = st.sidebar.number_input(
    "20 日均量下限（張）", 0, 1000000, step=100, key="g_min_volume",
    help="回測顯示冷門股的訊號雜訊很大，預設只看 20 日均量 500 張以上的股票",
)

st.sidebar.header("✅ 買進確認條件")
st.sidebar.caption("勾選後，買進法則 1～4 還要同時符合這些條件才算數；賣出、過熱不受影響。")
confirms = tuple(
    key for key, label in CONFIRMS.items()
    if st.sidebar.checkbox(label, key=f"confirm_{key}")
)

# ===== 計算訊號 =====
signals = granville_signals(
    prices, ma_days=ma_days, slope_days=slope_days, lookback=lookback,
    near_pct=near_pct, bias_pct=bias_pct, ins=ins, confirms=confirms,
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

# ===== 八大法則示意圖 =====
# 一張圖標出八個法則的位置。畫布 420×260，y 越小越高。
# 均線是一個完整的起伏（左邊谷底→中間高點→右邊下彎），股價繞著均線走。
def _ma_y(x):
    return 140 + 60 * math.cos(2 * math.pi * (x - 40) / 360)

_PRICE = [(0, 215), (25, 228), (55, 198), (80, 165), (100, 158), (118, 180), (135, 135), (148, 92),
          (168, 98), (185, 60), (200, 30), (220, 55), (250, 88), (268, 125), (290, 100), (310, 140),
          (322, 190), (342, 180), (360, 215), (378, 240), (400, 222), (420, 212)]
# 法則編號 → 圓點位置
_RULE_POINTS = {1: (55, 198), 2: (135, 135), 3: (168, 98), 8: (200, 30),
                5: (250, 88), 6: (310, 140), 7: (342, 180), 4: (378, 240)}


def granville_diagram():
    """八大法則位置示意圖（SVG 字串）"""
    ma = " ".join(f"{x},{_ma_y(x):.1f}" for x in range(0, 421, 5))
    price = " ".join(f"{x},{y}" for x, y in _PRICE)
    dots = ""
    for n, (x, y) in _RULE_POINTS.items():
        color = {"買進": UP_COLOR, "賣出": DOWN_COLOR}.get(RULES[n][0], WARN_COLOR)
        dots += (f'<circle cx="{x}" cy="{y}" r="10" fill="{color}" stroke="#0a0a0a" stroke-width="2"/>'
                 f'<text x="{x}" y="{y + 4}" text-anchor="middle" font-size="12" font-weight="700" '
                 f'fill="#ffffff" font-family="IBM Plex Mono, monospace">{n}</text>')
    return (
        f'<svg viewBox="0 0 420 260" width="100%" role="img" aria-label="葛蘭碧八大法則位置示意圖" '
        f'style="max-width:520px;background:#111111;border:1px solid #2a2a2a;border-radius:2px;display:block">'
        f'<polyline points="{ma}" fill="none" stroke="{MUTED}" stroke-width="2" stroke-dasharray="6 5"/>'
        f'<polyline points="{price}" fill="none" stroke="{ACCENT}" stroke-width="2.5" stroke-linejoin="round"/>'
        f'{dots}</svg>'
    )


with tab_today:
    st.caption(f"資料日期：{latest_date}　·　依 {ma_days} 日均線判斷，共 {len(df):,} 檔有足夠資料")

    with st.expander("📖 八大法則說明與判斷方式"):
        text_col, pic_col = st.columns([3, 2], vertical_alignment="center")
        text_col.markdown("\n".join(f"- **法則{n}（{side}·{level}）**：{desc}" for n, (side, desc, level) in RULES.items()))
        pic_col.markdown(granville_diagram(), unsafe_allow_html=True)
        pic_col.caption("橘線＝股價，灰色虛線＝均線；圓點數字是法則編號（紅＝買進、綠＝賣出、橘＝過熱）")
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

**買進確認條件（左側勾選）**
- 外資 5 日買超：近 5 個交易日外資買賣超合計 > 0。
- MACD 柱狀體 > 0：MACD(12, 26, 9) 的 DIF 高於訊號線。
- 當日量 > 1.5 倍 20 日均量：今天成交量是前 20 日平均的 1.5 倍以上（表格的「量比」）。

用 2025-12 ~ 2026-10 回測法則 2（持有 10 日相對大盤的超額報酬，分三段期間）：
單獨加 MACD 或量增，最近一段反而變差；三個一起用時三段分別從 +1.0%／+1.6%／+0.4% 提高到
+4.0%／+3.7%／+1.7%，勝率約五成，但每天只剩約 3 檔訊號。這個組合是看過資料後挑出來的，
建議用「模擬交易」分頁勾選前後比較，並持續觀察。RSI、KD 的「健康區間」條件回測沒有幫助，所以沒有加入。
""")
        st.caption("技術分析僅供參考，請搭配其他指標與基本面判斷。")


    col_map = {
        "stock_id":    "股票代號",
        "stock_name":  "公司名稱",
        "market":      "市場",
        "industry":    "產業別",
        "rule_text":   "符合法則",
        "close_price": "收盤價",
        "change":      "漲跌",
        "ma":          f"{ma_days}日均線",
        "bias":        "乖離率%",
        "ma_trend":    "均線方向",
        "volume":      "成交量(張)",
        "avg_volume_20": "20日均量(張)",
        "vol_ratio":   "量比",
        "foreign_5d":  "外資5日(張)",
        "macd_hist":   "MACD柱",
    }

    def style_trend(val):
        return style_sign({"上揚": 1, "下彎": -1}.get(val, 0))

    def show_rule_table(side_rules, key, label):
        """一個區塊：各法則檔數、法則選擇、結果表、下載按鈕"""
        cols = st.columns(len(side_rules))
        for col, n in zip(cols, side_rules):
            side, desc, level = RULES[n]
            col.metric(f"法則{n}（{side}·{level}）", f"{int(df['rules'].apply(lambda r: n in r).sum()):,} 檔",
                       help=desc)
        selected = st.multiselect(
            "顯示符合這些法則的股票", side_rules,
            default=[n for n in side_rules if n in STABLE_RULES],
            format_func=lambda n: f"法則{n}（{RULES[n][0]}·{RULES[n][2]}）",
            key=f"rules_{key}",
        )
        result = df.assign(rules=df["rules"].apply(lambda r: [n for n in r if n in selected]))
        result = result[result["rules"].str.len() > 0].copy()
        if result.empty:
            st.info("目前沒有股票符合選取的法則")
            return
        result["rule_text"] = result["rules"].apply(lambda r: "、".join(f"法則{n}" for n in r))
        result["first_rule"] = result["rules"].str[0]  # 依法則編號、股票代號排序
        result = result.sort_values(["first_rule", "stock_id"]).reset_index(drop=True)
        st.markdown(f"**{label}（{len(result):,} 檔）**")

        display_df = result[list(col_map)].rename(columns=col_map)
        change = result["change"]
        styler = display_df.style \
            .format("{:,.2f}", subset=["收盤價", "漲跌", f"{ma_days}日均線", "乖離率%"], na_rep="") \
            .format("{:,.0f}", subset=["成交量(張)", "20日均量(張)", "外資5日(張)"], na_rep="") \
            .format("{:.2f}", subset=["量比", "MACD柱"], na_rep="") \
            .map(style_sign, subset=["外資5日(張)", "MACD柱", "漲跌", "乖離率%"]) \
            .map(style_trend, subset=["均線方向"]) \
            .apply(lambda s: [style_sign(c) for c in change], subset=["收盤價"], axis=0)
        st.dataframe(
            styler,
            hide_index=True,
            width="stretch",
            height=min(600, 38 + 35 * len(display_df)),
            column_config={
                "股票代號": st.column_config.TextColumn(pinned=True),
                "公司名稱": st.column_config.TextColumn(pinned=True),
            },
        )
        st.download_button(
            label=f"📥 下載{label} CSV",
            data=display_df.to_csv(index=False, encoding="utf-8-sig"),
            file_name=f"葛蘭碧{label}_{latest_date}.csv",
            mime="text/csv",
            key=f"download_{key}",
        )
        return result

    # ===== 法則 1 觀察名單 =====
    with st.container(border=True):
        st.subheader("🔭 法則 1 觀察名單")
        lo, hi = HOLD_HINT
        R2_COLS = ("①均線上揚", "②曾跌破均線", "③站回均線", "④在季線上")
        st.caption(
            f"近期符合「法則 1 + 外資 5 日買超 + MACD 柱 > 0 + 當日量 > 1.5 倍均量」的股票（固定條件，不受左側勾選影響）。"
            f"起點後 {WATCH_DAYS} 個交易日內出現法則 2 → 隔天開盤買進、建議持有 {lo}～{hi} 個交易日；"
            f"超過 {WATCH_DAYS} 天沒出現視為過期。回測只有 24 筆交易，僅供參考。"
        )
        st.markdown(
            f"**法則 2 的判斷條件**（以下 4 項同時成立，參數跟左側設定連動；表格中①～④欄標「符合」或「觀察」）：  \n"
            f"① {ma_days} 日均線持續上揚：今天的均線高於 {slope_days} 天前，且 {slope_days} 天前也在上揚  \n"
            f"② 前 {lookback} 個交易日內，至少有一天收盤價跌破 {ma_days} 日均線  \n"
            f"③ 今天收盤價重新站回 {ma_days} 日均線之上  \n"
            f"④ {ma_days} 日均線在季線（60 日均線）之上"
        )
        watch = pd.DataFrame() if ins is None else rule1_watchlist(
            prices, ins=ins, min_volume=min_volume, ma_days=ma_days, slope_days=slope_days,
            lookback=lookback, near_pct=near_pct, bias_pct=bias_pct,
        )
        if not watch.empty:
            # 套用左側的代號、市場、產業篩選
            watch = watch[watch["stock_id"].isin(df["stock_id"])] \
                .merge(stock_list[["stock_id", "stock_name", "industry"]], on="stock_id", how="left")
        if ins is None:
            st.warning("法人資料讀取失敗，觀察名單暫時無法判斷外資條件。")
        elif watch.empty:
            st.info("近期沒有股票符合法則 1 + 三個確認條件")
        else:
            for col, status in zip(st.columns(len(WATCH_STATUS)), WATCH_STATUS):
                col.metric(status, f"{int((watch['status'] == status).sum()):,} 檔")
            show_late = st.checkbox("也顯示已錯過買點、已過期的股票", value=False, key="watch_show_late")
            if not show_late:
                watch = watch[~watch["status"].isin(LATE_STATUS)]
            watch["status_order"] = watch["status"].map({s: i for i, s in enumerate(WATCH_STATUS)})
            watch = watch.sort_values(["status_order", "setup_date"], ascending=[True, False])
            watch["note"] = watch.apply(watch_note, axis=1)
            watch["rule2_day"] = watch["rule2_day"].apply(lambda d: f"第 {int(d)} 天" if pd.notna(d) else "")
            watch["rule2_date"] = watch["rule2_date"].fillna("")
            watch_cols = {
                "stock_id": "股票代號", "stock_name": "公司名稱", "industry": "產業別",
                "status": "狀態", "setup_date": "符合法則1日期", "rule2_date": "法則2出現日期",
                "rule2_day": "起點後",
                **{f"r2_c{i}": name for i, name in enumerate(R2_COLS, 1)},
                "note": "提醒", "setup_close": "起點收盤價",
                "close_price": "最新收盤價", "change_since": "起點至今%",
            }
            watch_df = watch[list(watch_cols)].rename(columns=watch_cols)

            def style_part(val):
                return f"color:{UP_COLOR};font-weight:700;" if val == "符合" else "color:#94a3b8;"

            def style_status(val):
                return {
                    "今天出現法則2": f"color:{UP_COLOR};font-weight:700;",
                    "等待法則2": f"color:{WARN_COLOR};font-weight:700;",
                    "已錯過買點": "color:#94a3b8;",
                    "已過期": "color:#94a3b8;",
                }.get(val, "")

            if watch_df.empty:
                st.info("目前沒有今天出現法則 2 或等待中的股票，可勾選上方選項查看已錯過買點、已過期的股票")
            else:
                st.dataframe(
                    watch_df.style
                        .format("{:,.2f}", subset=["起點收盤價", "最新收盤價"], na_rep="")
                        .format("{:+.2f}", subset=["起點至今%"], na_rep="")
                        .map(style_sign, subset=["起點至今%"])
                        .map(style_status, subset=["狀態"])
                        .map(style_part, subset=list(R2_COLS)),
                    hide_index=True,
                    width="stretch",
                    height=min(500, 38 + 35 * len(watch_df)),
                    column_config={
                        "股票代號": st.column_config.TextColumn(pinned=True),
                        "公司名稱": st.column_config.TextColumn(pinned=True),
                    },
                )

    # ===== 買進訊號 =====
    with st.container(border=True):
        st.subheader("🔴 買進訊號")
        if confirms:
            st.caption("已套用買進確認條件：" + "、".join(CONFIRMS[c] for c in confirms))
        buy_result = show_rule_table([1, 2, 3, 4], "buy", "買進訊號")
        # 觀察名單與買進訊號的股票都可以加入模擬持股，以最新收盤價假設買進
        picks = [f[["stock_id", "stock_name", "close_price"]] for f in (watch, buy_result)
                 if f is not None and not f.empty and "stock_name" in f.columns]
        add_form(pd.concat(picks, ignore_index=True) if picks else pd.DataFrame(columns=["stock_id", "stock_name", "close_price"]),
                 "葛蘭碧", latest_date, "granville")

    # ===== 賣出訊號 =====
    with st.container(border=True):
        st.subheader("🟢 賣出訊號與過熱提醒")
        st.caption("法則 8（正乖離過大）回測多半續漲，列在這裡只當追高風險提醒。")
        show_rule_table([5, 6, 7, 8], "sell", "賣出訊號")

    # ===== 個股走勢 =====
    with st.container(border=True):
        st.subheader("📈 個股走勢")
        st.caption(f"近 3 個月收盤價（還原權值）與 {ma_days} 日均線，圓點是當天符合的法則"
                   "（紅＝買進、綠＝賣出、橘＝過熱；只看均線條件，不含左側的買進確認條件）。")
        chart_df = df.assign(has_rule=df["rules"].str.len() > 0) \
            .sort_values(["has_rule", "stock_id"], ascending=[False, True])
        chart_labels = {
            r.stock_id: f"{r.stock_id} {r.stock_name}"
            + ("｜" + "、".join(f"法則{n}" for n in r.rules) if r.rules else "")
            for r in chart_df.itertuples()
        }
        if not chart_labels:
            st.info("目前篩選條件下沒有股票")
        else:
            c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
            chart_id = c1.selectbox("選擇股票（有訊號的排在前面）", list(chart_labels),
                                    format_func=chart_labels.get, key="chart_stock")
            if c2.button("前往個股分析卡", width="stretch"):
                st.session_state["card_stock_id"] = chart_id
                st.switch_page("pages/2_個股分析卡.py")
            one = prices[prices["stock_id"] == chart_id]
            close_wide = one.assign(close_price=pd.to_numeric(one["close_price"], errors="coerce")) \
                .pivot_table(index="date", columns="stock_id", values="close_price", aggfunc="last").sort_index()
            frames = rule_frames(close_wide, ma_days, slope_days, lookback, near_pct, bias_pct)
            line = pd.DataFrame({
                "date": pd.to_datetime(close_wide.index),
                "收盤價": close_wide[chart_id].values,
                "均線": frames["ma"][chart_id].round(2).values,
            })
            marks = []
            for n, hit in frames["hits"].items():
                for d in hit.index[hit[chart_id].fillna(False).astype(bool)]:
                    marks.append({"date": d, "rule": n, "side": RULES[n][0]})
            base = alt.Chart(line).encode(x=alt.X("date:T", title=None, axis=alt.Axis(format="%m/%d")))
            layers = [
                base.mark_line(color=MUTED, strokeDash=[6, 4]).encode(
                    y=alt.Y("均線:Q", title="股價", scale=alt.Scale(zero=False))),
                base.mark_line(color=ACCENT, strokeWidth=2).encode(
                    y=alt.Y("收盤價:Q", scale=alt.Scale(zero=False)),
                    tooltip=[alt.Tooltip("date:T", title="日期"),
                             alt.Tooltip("收盤價:Q", format=",.2f"), alt.Tooltip("均線:Q", format=",.2f")]),
            ]
            if marks:
                pts = pd.DataFrame(marks).groupby("date").agg(
                    rule_text=("rule", lambda r: "、".join(str(n) for n in sorted(r))),
                    side=("side", "first"),
                ).reset_index()
                pts = pts.merge(line.assign(date=line["date"].dt.strftime("%Y-%m-%d")), on="date")
                pts["date"] = pd.to_datetime(pts["date"])
                color = alt.Color("side:N", legend=None, scale=alt.Scale(
                    domain=["買進", "賣出", "過熱"], range=[UP_COLOR, DOWN_COLOR, WARN_COLOR]))
                point_base = alt.Chart(pts).encode(x="date:T", y="收盤價:Q")
                layers += [
                    point_base.mark_circle(size=90, opacity=1).encode(
                        color=color,
                        tooltip=[alt.Tooltip("date:T", title="日期"), alt.Tooltip("rule_text:N", title="法則"),
                                 alt.Tooltip("收盤價:Q", format=",.2f")]),
                    point_base.mark_text(dy=-12, fontSize=11, fontWeight="bold").encode(
                        text="rule_text:N", color=color),
                ]
            st.altair_chart(alt.layer(*layers).properties(height=320), width="stretch")


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
def load_sim_ins(calendar_days):
    with st.spinner(f"正在載入近 {calendar_days} 天法人買賣超..."):
        return get_recent_ins(calendar_days=calendar_days)


@st.cache_data(ttl=3600)
def run_simulation(period_days, buy_rules, sell_rules, hold_days, stop_loss_pct, min_vol, params,
                   confirms=()):
    sim_prices = load_sim_prices(period_days + WARMUP_DAYS)
    if sim_prices.empty:
        return pd.DataFrame(), None
    # 外資條件才需要法人資料
    sim_ins = load_sim_ins(period_days + WARMUP_DAYS) if "foreign" in confirms else None
    start = (pd.to_datetime(sim_prices["date"].max()) - pd.Timedelta(days=period_days)).strftime("%Y-%m-%d")
    return simulate_trades(
        sim_prices, list(buy_rules), list(sell_rules), hold_days=hold_days,
        stop_loss_pct=stop_loss_pct or None, min_volume=min_vol, start_date=start,
        ins=sim_ins, confirms=confirms, **dict(params),
    )


def apply_recommended():
    """頁面說明中回測較好的組合：法則 2 + 三個買進確認條件，持有 10 個交易日"""
    st.session_state.update({"sim_buy": [2], "sim_hold": 10,
                             **{f"confirm_{key}": True for key in CONFIRMS}})


st.session_state.setdefault("sim_buy", [2])
st.session_state.setdefault("sim_hold", 20)

with tab_sim:
    st.markdown(
        "用過去的股價假設「照著法則買賣」，看看會賺還是賠。"
        "收盤出現買進訊號 → **隔天開盤買進**；出現賣出訊號或跌破停損 → **隔天開盤賣出**；"
        "持有滿設定天數 → 當天收盤賣出。每檔股票同時只持有一筆，報酬已扣手續費與證交稅。"
    )
    st.button("⭐ 套用建議組合", on_click=apply_recommended,
              help="買進法則 2 + 左側三個買進確認條件全部勾選 + 最長持有 10 個交易日（頁面說明中回測較好的組合）")
    s1, s2, s3 = st.columns(3)
    with s1:
        sim_period = st.selectbox("回測期間", list(SIM_PERIODS), index=1)
        sim_buy = st.multiselect(
            "買進法則", [1, 2, 3, 4],
            format_func=lambda n: f"法則{n}（{RULES[n][2]}）", key="sim_buy",
        )
    with s2:
        sim_sell = st.multiselect(
            "賣出法則（出現就出場）", [5, 6, 7, 8], default=[6, 7],
            format_func=lambda n: f"法則{n}（{RULES[n][0]}·{RULES[n][2]}）",
        )
        sim_hold = st.number_input("最長持有天數（交易日）", 1, 120, key="sim_hold")
    with s3:
        sim_stop = st.number_input("停損（%，0 表示不停損）", 0.0, 50.0, 8.0, step=1.0)
        sim_amount = st.number_input("每筆投入金額（元）", 10000, 10000000, 100000, step=10000)
    st.caption(
        f"均線參數與左側相同（{ma_days} 日均線），只買 20 日均量 ≥ {min_volume:,} 張的股票；"
        "買進確認條件："
        + ("、".join(CONFIRMS[c] for c in confirms) if confirms else "未勾選")
        + "（在左側勾選）。市場、產業、代號篩選不套用在模擬上。"
    )

    if st.button("▶️ 開始模擬", type="primary"):
        st.session_state["granville_sim"] = True

    if not sim_buy:
        st.info("請至少選一條買進法則")
    elif st.session_state.get("granville_sim"):
        params = (("ma_days", ma_days), ("slope_days", slope_days), ("lookback", lookback),
                  ("near_pct", near_pct), ("bias_pct", bias_pct))
        try:
            trades, _ = run_simulation(
                SIM_PERIODS[sim_period], tuple(sorted(sim_buy)), tuple(sorted(sim_sell)),
                int(sim_hold), float(sim_stop), int(min_volume), params, confirms,
            )
        except Exception:
            trades = None
            st.warning("模擬用的股價或法人資料暫時讀取失敗，請稍後再按一次「開始模擬」。")
        if trades is None:
            pass
        elif trades.empty:
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

            # 已實現損益累積曲線（依出場日），對照：同樣的日子、同樣的金額改買同期大盤（全部股票等權平均）
            curve = closed.assign(
                法則=closed["return_pct"] / 100 * sim_amount,
                同期大盤=closed["benchmark_pct"] / 100 * sim_amount,
            ).groupby("exit_date")[["法則", "同期大盤"]].sum().cumsum().reset_index()
            curve["exit_date"] = pd.to_datetime(curve["exit_date"])
            if not curve.empty:
                curve_long = curve.melt("exit_date", var_name="series", value_name="pnl")
                curve_long["series"] = curve_long["series"].map(
                    {"法則": "照法則買賣", "同期大盤": "同期大盤（等權平均）"})
                st.altair_chart(
                    alt.Chart(curve_long).mark_line().encode(
                        x=alt.X("exit_date:T", title="出場日", axis=alt.Axis(format="%m/%d")),
                        y=alt.Y("pnl:Q", title="已實現累積損益（元）"),
                        color=alt.Color("series:N", title=None, legend=alt.Legend(orient="top"),
                                        scale=alt.Scale(domain=["照法則買賣", "同期大盤（等權平均）"],
                                                        range=[ACCENT, MUTED])),
                        strokeDash=alt.StrokeDash("series:N", legend=None,
                                                  scale=alt.Scale(domain=["照法則買賣", "同期大盤（等權平均）"],
                                                                  range=[[1, 0], [6, 4]])),
                        tooltip=[alt.Tooltip("exit_date:T", title="出場日"),
                                 alt.Tooltip("series:N", title="線"),
                                 alt.Tooltip("pnl:Q", title="累積損益", format=",.0f")],
                    ).properties(height=300),
                    width="stretch",
                )
                st.caption("灰色虛線：每筆交易同樣的持有期間、同樣的金額，改成買全部股票的等權平均，"
                           "已實現損益會是多少（大盤報酬未扣手續費）。")

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
                "⚠️ 模擬限制：股價已還原除權息（配息、配股不會被當成下跌）；手續費以牌價 0.1425% 計（未打折）；"
                "假設開盤一定買得到、賣得掉（漲跌停鎖死時實際上可能成交不了）；"
                "同期大盤是全部股票的等權平均漲跌。過去結果不代表未來表現。"
            )
    else:
        st.info("設定好條件後，按「開始模擬」。")

