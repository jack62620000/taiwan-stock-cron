import streamlit as st
import pandas as pd
import altair as alt
import sys
import os
import math

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.glossary import render_page_guide
from utils.db import get_stock_list, get_recent_closes, get_recent_ins, get_watchlist, get_paper_trades
from utils.granville import (
    RULES, STABLE_RULES, CONFIRMS, HOLD_HINT, WATCH_DAYS, WATCH_STATUS, LATE_STATUS, CHOP_DAYS, CHOP_CROSSES,
    STOP_LOSS_PCT, STOP_RULE, ATR_MULT, ATR_DAYS, RS_DAYS, granville_signals, ma_cross_counts, next_day_triggers, relative_strength, rule1_watchlist, rule_frames, watch_note,
)
from utils.watch import add_watch_form
from utils.analysis import (
    FOLLOW_DAYS, granville_buy_table, granville_followup, granville_rebound_table, rebound_followup, load_atr, render_followup, render_market_warning,
)
from utils.groups import current_group
from utils.ui import style_sign, UP_COLOR, DOWN_COLOR, WARN_COLOR, ACCENT, MUTED

# 選股頁的「葛蘭碧」分頁（pages/0_選股.py 用 runpy 執行這個檔案；登入、頁面設定、重新整理按鈕在選股頁）
render_page_guide("granville")

render_market_warning()

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

RULE_PARAMS = {"ma_days": ma_days, "slope_days": slope_days, "lookback": lookback,
               "near_pct": near_pct, "bias_pct": bias_pct}

# 法則 1 觀察名單（買賣預告、進階分頁共用），只留左側篩選後的股票
watch_all = pd.DataFrame() if ins is None else rule1_watchlist(prices, ins=ins, min_volume=min_volume, **RULE_PARAMS)
if not watch_all.empty:
    watch_all = watch_all[watch_all["stock_id"].isin(df["stock_id"])] \
        .merge(stock_list[["stock_id", "stock_name", "industry"]], on="stock_id", how="left")

tab_plan, tab_today, tab_follow = st.tabs(["🎯 買賣預告", "📋 進階：八大法則", "🧾 事後追蹤"])

# ===== 個股走勢圖 =====
def render_stock_chart(labels, triggers):
    """
    labels：{stock_id: 選單文字}，依顯示順序；triggers：{stock_id: (價格, 說明)}，在圖上畫成水平虛線。
    """
    st.subheader("📈 個股走勢")
    st.caption(f"近 3 個月收盤價（還原權值）與 {ma_days} 日均線，圓點是當天符合的法則"
               "（紅＝買進、綠＝賣出、橘＝過熱；只看均線條件，不含買進確認條件），白色虛線是明天的觸發價。")
    if not labels:
        st.info("目前篩選條件下沒有股票")
        return
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    chart_id = c1.selectbox("選擇股票（預告清單的股票排在前面）", list(labels),
                            format_func=labels.get, key="chart_stock")
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
    if chart_id in triggers:
        price, text = triggers[chart_id]
        ref = alt.Chart(pd.DataFrame({"y": [price], "label": [text]}))
        layers += [
            ref.mark_rule(color="#e2e8f0", strokeDash=[2, 3]).encode(y="y:Q"),
            ref.mark_text(align="left", dx=4, dy=-7, color="#e2e8f0", fontSize=11)
                .encode(y="y:Q", x=alt.value(0), text="label:N"),
        ]
    st.altair_chart(alt.layer(*layers).properties(height=320), width="stretch")


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
        dots += (f'<circle cx="{x}" cy="{y}" r="10" fill="{color}" stroke="#050b1a" stroke-width="2"/>'
                 f'<text x="{x}" y="{y + 4}" text-anchor="middle" font-size="12" font-weight="700" '
                 f'fill="#ffffff" font-family="IBM Plex Mono, monospace">{n}</text>')
    return (
        f'<svg viewBox="0 0 420 260" width="100%" role="img" aria-label="葛蘭碧八大法則位置示意圖" '
        f'style="max-width:520px;background:#0b1631;border:1px solid #1e3a6e;border-radius:2px;display:block">'
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
        pic_col.caption("金線＝股價，灰色虛線＝均線；圓點數字是法則編號（紅＝買進、綠＝賣出、橘＝過熱）")
        st.markdown(f"""
**程式怎麼判斷（以最新一個交易日為準，參數可在左側調整）**
- 均線上揚／下彎：今天的均線比 {slope_days} 天前高／低。
- 法則 1、5：今天剛突破／跌破均線，而且 {slope_days} 天前的均線方向是相反或走平的（代表均線正在轉折）。
- 法則 2、6：均線方向一直沒變，過去 {lookback} 天內曾經跌破／突破均線，今天又回到原本那一側；法則 2 另外要求均線在季線（60 日均線）之上。
- 法則 3、7：過去 {lookback} 天都在均線同一側，最接近均線時乖離在 {near_pct}% 以內，今天往遠離均線的方向走。
- 法則 4、8：乖離率（收盤價與均線的差距）超過 ±{bias_pct}%。

**可靠度怎麼來的**
用 2025-02 ~ 2026-10 的資料回測（20 日均量 500 張以上、訊號隔天開盤買、持有 20 個交易日，和同一天所有股票平均比較），
分成前後兩段期間：兩段方向一致的標「穩定」，兩段結果相反、差距太小或樣本太少的標「參考」。
- 法則 2、4、7 兩段都和預期方向一致（法則 2 比平均 +1.0%、法則 4 +2.0%、法則 7 −1.3%）。
- 法則 4（急跌反彈）改成隔天開盤買後兩段都明顯較好，從參考改為穩定。
- 法則 6 前段幾乎沒差、只有後段偏弱，從穩定改為參考。
- 法則 8 原本是賣訊，但兩段期間都是續漲（比平均 +1.7%），所以當「過熱」提醒。
- 法則 1 兩段都偏正但前段差距很小、樣本少；法則 3 和平均一樣；法則 5 反而略好於平均。這幾條預設不顯示，可在下方自行勾選。
- 單看訊號，所有法則持有 20 日的勝率都不到五成，平均值是靠少數大漲股撐起來。

**買進確認條件（左側勾選）**
- 外資 5 日買超：近 5 個交易日外資買賣超合計 > 0。
- MACD 柱狀體 > 0：MACD(12, 26, 9) 的 DIF 高於訊號線。
- 當日量 > 1.5 倍 20 日均量：今天成交量是前 20 日平均的 1.5 倍以上（表格的「量比」）。

用 2025-12 ~ 2026-10 回測法則 2（持有 10 日相對大盤的超額報酬，分三段期間）：
單獨加 MACD 或量增，最近一段反而變差；三個一起用時三段分別從 +1.0%／+1.6%／+0.4% 提高到
+4.0%／+3.7%／+1.7%，勝率約五成，但每天只剩約 3 檔訊號。這個組合是看過資料後挑出來的，
建議持續觀察。RSI、KD 的「健康區間」條件回測沒有幫助，所以沒有加入。
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
        watch = watch_all.copy()
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
        show_rule_table([1, 2, 3, 4], "buy", "買進訊號")

    # ===== 賣出訊號 =====
    with st.container(border=True):
        st.subheader("🟢 賣出訊號與過熱提醒")
        st.caption("法則 8（正乖離過大）回測多半續漲，列在這裡只當追高風險提醒。")
        show_rule_table([5, 6, 7, 8], "sell", "賣出訊號")


# =====================================================================
# 買賣預告：推算明天收盤到多少會出現訊號
# =====================================================================
@st.cache_data(ttl=3600)
def load_triggers(rules, side, stock_ids, params):
    return next_day_triggers(load_prices(), list(rules), side, stock_ids=list(stock_ids), **dict(params))


def my_stock_ids():
    """目前分組的自選股與持有中的模擬持股"""
    ids = set()
    try:
        gid, _ = current_group()
    except Exception:
        return ids
    try:
        ids |= set(get_watchlist(gid)["stock_id"])
    except Exception:
        pass
    try:
        trades = get_paper_trades(gid)
        if not trades.empty:
            ids |= set(trades[trades["sell_date"].isna()]["stock_id"])
    except Exception:
        pass
    return ids


SELL_RULE_TEXT = {5: "法則5（均線轉弱、跌破均線）", 6: "法則6（反彈失敗、跌回均線下）"}

with tab_plan:
    params_key = tuple(RULE_PARAMS.items())
    lo, hi = HOLD_HINT
    st.caption(
        f"資料日期：{latest_date}（收盤後）。觸發價是推算「下一個交易日收盤」要到多少才會出現訊號；"
        f"依 {ma_days} 日均線，只看 20 日均量 ≥ {min_volume:,} 張的股票（參數、篩選在左側）。"
    )
    with st.expander("💡 這一頁怎麼看"):
        st.markdown(f"""
- **準備買進**：先出現法則 1（股價突破剛轉折的均線，而且外資買超、MACD 翻正、成交量放大），之後股價回檔跌破均線、
  再站回均線，就是法則 2 的買點。回測是法則 2 出現的**隔天開盤買進，持有 {lo}～{hi} 個交易日**。
- **觸發價**：用最近的收盤價算出來。例如「明天收盤 ≥ 52.02 元」，代表明天收盤只要在 52.02 元以上，法則 2 就成立，後天開盤就是買點。
- **停損價**：{STOP_RULE}。回測法則 2 持有 {lo}～{hi} 日，不停損最差一筆虧 38%，停損 {STOP_LOSS_PCT:g}% 最差約虧 17%，
  平均報酬只少一點（+0.86% → +0.66%）；停損設太緊（例如 5%）反而有一半的交易被洗出場。
  ATR 是近 {ATR_DAYS} 日每天平均的波動幅度：波動小的股票用 {ATR_MULT:g} 倍 ATR，停損會比 {STOP_LOSS_PCT:g}% 貼近；
  波動大的股票仍以 {STOP_LOSS_PCT:g}% 為上限，大虧的上限不變。
- **每張風險**：買在預計價、跌到停損價出場，一張（1000 股）會虧多少元。可以用「每筆最多願意虧的錢 ÷ 每張風險」決定買幾張。
- **比大盤強弱**：近 {RS_DAYS} 個交易日漲跌幅減去全部股票的平均，正的代表比大盤強；同一個狀態裡，強於大盤的排前面。
- **急跌反彈候選**：法則 4，均線往下、股價跌到離均線很遠。回測隔天開盤買、持有 20 日比平均好約 2%，但勝率只有四成多，一定要設停損。
- **賣出警示**：均線已經走平或往下，明天收盤跌破觸發價就會出現賣出訊號（法則 5、6）。手上有的股票可以先記下這個價位。
- **過熱（法則 8）**：只對自選股與持有中的模擬持股提醒。回測顯示離均線越遠、之後平均還是續漲，所以不建議急著賣，
  而是把停損往上移到均線附近，鎖住大部分獲利。
- 法則 1 出現後 {WATCH_DAYS} 個交易日內沒等到法則 2，就視為失效，不再列出。
- 觸發價只代表「如果收盤到這個價位」，不代表一定會發生；技術分析僅供參考。完整的八條法則在「進階」分頁。
""")

    # ===== 準備買進 =====
    chart_labels, chart_triggers = {}, {}
    with st.container(border=True):
        st.subheader("🔴 準備買進")
        st.caption(f"最近出現法則 1 的股票，等法則 2 出現再買。「🌊 盤整」是近 {CHOP_DAYS} 個交易日穿越均線 "
                   f"{CHOP_CROSSES} 次以上的股票；回測一年的法則 2，盤整股的報酬並沒有比較差，所以只當參考，不影響排序。")
        if ins is None:
            st.warning("法人資料讀取失敗，暫時無法判斷法則 1 的外資條件。")
            cand = pd.DataFrame()
        elif watch_all.empty:
            cand = pd.DataFrame()
        else:
            cand = watch_all[watch_all["status"].isin(["今天出現法則2", "等待法則2"])]
        if ins is not None and cand.empty:
            st.info("目前沒有出現法則 1、正在等待法則 2 的股票")
        elif not cand.empty:
            waiting = tuple(sorted(cand.loc[cand["status"] == "等待法則2", "stock_id"]))
            trig = load_triggers((2,), "買進", waiting, params_key).set_index("stock_id")
            ma_now = df.set_index("stock_id")["ma"]
            crosses = ma_cross_counts(prices, ma_days)
            rs = relative_strength(prices)
            atrs = load_atr(tuple(sorted(cand["stock_id"])))
            buy_df, buy_triggers = granville_buy_table(cand, trig, ma_now, crosses, rs, atrs)
            chart_triggers.update(buy_triggers)
            m1, m2, m3 = st.columns(3)
            m1.metric("明天開盤可買", f"{(buy_df['狀態'] == '✅ 明天開盤可買').sum()} 檔")
            m2.metric("有觸發價、等待法則2", f"{(buy_df['狀態'] == '⏳ 等待法則2').sum()} 檔")
            m3.metric("觀察中", f"{(buy_df['狀態'] == '👀 觀察中').sum()} 檔")
            st.dataframe(
                buy_df.style
                    .format("{:,.2f}", subset=["最新收盤", "觸發價", "停損價"], na_rep="")
                    .format("{:,.0f}", subset=["每張風險"], na_rep="")
                    .format("{:+.2f}", subset=["距離%", "比大盤%"], na_rep="")
                    .map(style_sign, subset=["距離%", "比大盤%"]),
                hide_index=True, width="stretch", height=min(500, 38 + 35 * len(buy_df)),
                column_config={"代號": st.column_config.TextColumn(pinned=True),
                               "名稱": st.column_config.TextColumn(pinned=True),
                               "距離%": st.column_config.Column(help="觸發價比今天收盤高（+）或低（−）幾 %"),
                               "比大盤%": st.column_config.Column(
                                   help=f"近 {RS_DAYS} 個交易日漲跌幅減去全部股票平均，正的代表比大盤強"),
                               "停損價": st.column_config.Column(
                                   help=STOP_RULE + "，收盤跌破就出場"),
                               "每張風險": st.column_config.Column(
                                   help="（預計買進價 − 停損價）× 1000 股，單位：元"),
                               "穿越均線": st.column_config.NumberColumn(
                                   help=f"近 {CHOP_DAYS} 個交易日收盤價站上或跌破均線的次數，越少代表趨勢越明確"),
                               "說明": st.column_config.TextColumn(width="large")},
            )
            add_watch_form(buy_df.rename(columns={"代號": "stock_id", "名稱": "stock_name"}),
                           "葛蘭碧", latest_date, "granville")
            for r in buy_df.itertuples():
                chart_labels[r.代號] = f"{r.代號} {r.名稱}｜{r.狀態}"

    # ===== 急跌反彈候選（法則 4）=====
    with st.container(border=True):
        st.subheader("🔵 急跌反彈候選（法則 4）")
        st.caption(f"均線往下、股價跌到離均線 {bias_pct:g}% 以上的股票（負乖離過大）。回測隔天開盤買、持有 20 個交易日，"
                   "前後兩段都比全部股票平均好（約 +2%），但勝率只有四成多，是靠少數大反彈撐起來，買之前一定要設停損。")
        if confirms:
            st.caption("⚠️ 左側勾了買進確認條件，法則 4 也要同時符合；回測法則 4 加上確認條件的樣本太少、結果反而很差，建議不要勾。")
        reb_df = granville_rebound_table(df, relative_strength(prices))
        if reb_df.empty:
            st.info("今天沒有出現法則 4 的股票")
        else:
            st.metric("今天出現法則 4", f"{len(reb_df)} 檔")
            st.dataframe(
                reb_df.style
                    .format("{:,.2f}", subset=["最新收盤", "均線"], na_rep="")
                    .format("{:+.2f}", subset=["乖離%", "比大盤%"], na_rep="")
                    .format("{:,.0f}", subset=["20日均量"], na_rep="")
                    .map(style_sign, subset=["乖離%", "比大盤%"]),
                hide_index=True, width="stretch", height=min(500, 38 + 35 * len(reb_df)),
                column_config={"代號": st.column_config.TextColumn(pinned=True),
                               "名稱": st.column_config.TextColumn(pinned=True),
                               "乖離%": st.column_config.Column(help="收盤價比均線低幾 %，越負代表跌得越深"),
                               "比大盤%": st.column_config.Column(
                                   help=f"近 {RS_DAYS} 個交易日漲跌幅減去全部股票平均"),
                               "20日均量": st.column_config.Column(help="單位：張")},
            )
            add_watch_form(reb_df.rename(columns={"代號": "stock_id", "名稱": "stock_name"}),
                           "葛蘭碧法則4", latest_date, "granville4")
            for r in reb_df.itertuples():
                chart_labels.setdefault(r.代號, f"{r.代號} {r.名稱}｜急跌反彈候選")

    # ===== 賣出警示 =====
    with st.container(border=True):
        st.subheader("🟢 賣出警示")
        st.caption("均線已經走平或往下，明天收盤跌破觸發價就會出現賣出訊號。自選股與持有中的模擬持股如果離均線太遠，也會標成「🔥 過熱」。")
        c1, c2 = st.columns([2, 1], vertical_alignment="bottom")
        only_mine = c1.toggle("只看自選股與持有中的模擬持股", value=False)
        max_drop = c2.number_input("列出再跌幾 % 以內就會出現的", 1.0, 10.0, 3.0, step=0.5)
        base = df[df["stock_id"].isin(my_stock_ids())] if only_mine else df
        today_sell = base[base["rules"].apply(lambda r: any(n in r for n in SELL_RULE_TEXT)).astype(bool)]
        rest = tuple(sorted(set(base["stock_id"]) - set(today_sell["stock_id"])))
        trig = load_triggers(tuple(SELL_RULE_TEXT), "賣出", rest, params_key)
        trig = trig[trig["trigger_pct"] >= -max_drop]
        info = base.set_index("stock_id")
        rows = []
        for r in today_sell.itertuples():
            names = "、".join(SELL_RULE_TEXT[n] for n in r.rules if n in SELL_RULE_TEXT)
            rows.append({"order": 0, "狀態": "⚠️ 今天已出現", "代號": r.stock_id, "名稱": r.stock_name,
                         "產業": r.industry, "最新收盤": r.close_price, "觸發價": None, "距離%": None,
                         "均線": r.ma, "說明": f"今天出現{names}：手上有的話，可考慮明天開盤賣出"})
        for t in trig.itertuples():
            r = info.loc[t.stock_id]
            names = "、".join(SELL_RULE_TEXT[n] for n in t.rules)
            rows.append({"order": 1, "狀態": "🔔 接近賣出", "代號": t.stock_id, "名稱": r["stock_name"],
                         "產業": r["industry"], "最新收盤": r["close_price"], "觸發價": t.trigger_price,
                         "距離%": t.trigger_pct, "均線": r["ma"],
                         "說明": (f"明天收盤 ≤ {t.trigger_price:,.2f} 元（跌 {-t.trigger_pct:.2f}% 以上）會出現{names}"
                                if t.trigger_pct < 0 else
                                f"明天收盤只要沒站上 {t.trigger_price:,.2f} 元（漲幅不到 {t.trigger_pct:.2f}%）就會出現{names}")})
            chart_triggers[t.stock_id] = (t.trigger_price, f"收盤 ≤ {t.trigger_price:,.2f} 出現賣出訊號")
        # 法則 8 過熱：回測多半續漲，只提醒手上有的股票把停損往上移
        listed = {row["代號"] for row in rows}
        hot = df[df["stock_id"].isin(my_stock_ids()) & ~df["stock_id"].isin(listed)
                 & df["rules"].apply(lambda r: 8 in r)]
        for r in hot.itertuples():
            rows.append({"order": 2, "狀態": "🔥 過熱（法則8）", "代號": r.stock_id, "名稱": r.stock_name,
                         "產業": r.industry, "最新收盤": r.close_price, "觸發價": None, "距離%": None, "均線": r.ma,
                         "說明": f"離均線 {r.bias:+.1f}%。回測多半續漲，不必急著賣；可把停損移到均線（約 {r.ma:,.2f} 元）鎖住獲利"})
        if not rows:
            st.info("目前沒有出現或接近賣出訊號的股票")
        else:
            sell_df = pd.DataFrame(rows).sort_values(["order", "距離%", "代號"], ascending=[True, False, True]) \
                .drop(columns=["order"])
            m1, m2, m3 = st.columns(3)
            m1.metric("今天已出現賣出訊號", f"{(sell_df['狀態'] == '⚠️ 今天已出現').sum()} 檔")
            m2.metric(f"再跌 {max_drop:g}% 以內會出現", f"{(sell_df['狀態'] == '🔔 接近賣出').sum()} 檔")
            m3.metric("持股過熱（法則8）", f"{(sell_df['狀態'] == '🔥 過熱（法則8）').sum()} 檔")
            st.dataframe(
                sell_df.style
                    .format("{:,.2f}", subset=["最新收盤", "觸發價", "均線"], na_rep="")
                    .format("{:+.2f}", subset=["距離%"], na_rep="")
                    .map(style_sign, subset=["距離%"]),
                hide_index=True, width="stretch", height=min(500, 38 + 35 * len(sell_df)),
                column_config={"代號": st.column_config.TextColumn(pinned=True),
                               "名稱": st.column_config.TextColumn(pinned=True),
                               "距離%": st.column_config.Column(help="觸發價比今天收盤低（−）或高（+）幾 %"),
                               "說明": st.column_config.TextColumn(width="large")},
            )
            for r in sell_df.itertuples():
                chart_labels.setdefault(r.代號, f"{r.代號} {r.名稱}｜{r.狀態}")

    # ===== 個股走勢 =====
    with st.container(border=True):
        for r in df.sort_values("stock_id").itertuples():
            chart_labels.setdefault(r.stock_id, f"{r.stock_id} {r.stock_name}")
        render_stock_chart(chart_labels, chart_triggers)


# ===== 事後追蹤：最近出現過的法則 2 買點，之後實際漲跌多少 =====
@st.cache_data(ttl=3600)
def load_followup():
    return granville_followup()


@st.cache_data(ttl=3600)
def load_rebound_followup():
    return rebound_followup()


with tab_follow:
    st.caption(f"最近 {FOLLOW_DAYS} 個交易日出現過的法則 2 買點（「準備買進」表裡「今天出現法則2」的股票），"
               f"之後實際漲跌多少，用來看最近的訊號準不準。固定用預設參數（20 日均線、20 日均量 ≥ 500 張），"
               f"不受左側設定影響。回測建議持有 {HOLD_HINT[0]}～{HOLD_HINT[1]} 個交易日。")
    # 要重算近 160 個日曆天的買點，第一次約 15 秒，按了才算（之後一小時內直接顯示）
    f_date, follow = None, None
    if st.toggle("顯示事後追蹤", key="granville_followup",
                 help="第一次要重算最近的買點，約 15 秒；之後一小時內直接顯示"):
        try:
            with st.spinner("計算最近的法則 2 買點…"):
                f_date, follow = load_followup()
        except Exception:
            st.warning("事後追蹤資料暫時讀取失敗，請稍後按「強制重新整理」再看一次。")
    if follow is not None and follow.empty:
        st.info(f"最近 {FOLLOW_DAYS} 個交易日沒有出現法則 2 買點。")
    elif follow is not None:
        st.caption(f"資料日期：{f_date}（收盤後），共 {len(follow)} 個買點。")
        render_followup(follow, {"industry": st.column_config.TextColumn("產業", width="small")})

    st.divider()
    st.markdown("**🔵 急跌反彈候選（法則 4）**")
    st.caption(f"最近 {FOLLOW_DAYS} 個交易日剛出現法則 4（均線往下、收盤離均線 10% 以上）的股票，隔天開盤買進到今天漲跌多少。"
               "同一檔連續好幾天都符合只算第一天。回測持有 20 個交易日比平均好約 2%，但勝率只有四成多。")
    r_date, rebound = None, None
    if st.toggle("顯示急跌反彈的事後追蹤", key="rebound_followup", help="第一次要重算，約幾秒；之後一小時內直接顯示"):
        try:
            with st.spinner("計算最近的法則 4…"):
                r_date, rebound = load_rebound_followup()
        except Exception:
            st.warning("事後追蹤資料暫時讀取失敗，請稍後按「強制重新整理」再看一次。")
    if rebound is not None and rebound.empty:
        st.info(f"最近 {FOLLOW_DAYS} 個交易日沒有剛出現法則 4 的股票。")
    elif rebound is not None:
        st.caption(f"資料日期：{r_date}（收盤後），共 {len(rebound)} 檔。")
        render_followup(rebound, {"industry": st.column_config.TextColumn("產業", width="small"),
                                  "bias": st.column_config.NumberColumn("出現時乖離%", format="%+.2f")})
