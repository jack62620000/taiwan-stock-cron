import streamlit as st
import pandas as pd
import numpy as np
import altair as alt
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.db import (
    get_stock_info, get_daily_prices, get_quarterly,
    get_revenue, get_stock_ins, get_margin, get_holder,
    get_margin_utilization, get_stock_list, get_market_index,
    get_watchlist, add_to_watchlist, remove_from_watchlist, get_signal_history, get_rows_for_stocks,
)
from utils.analysis import granville_status
from utils.granville import STOP_RULE, stop_price
from utils.momentum import BACKTEST_MD, RULES_MD, conditions
from utils.indicators import calc_all, get_latest_signals
from utils.groups import current_group
from utils.ui import require_login, render_metric_cards, UP_COLOR, DOWN_COLOR, ACCENT, GLOW
from utils.dashboard import (
    DASH_CSS, LIGHTS, energy_blocks, gauge_svg, grade_of, level_color, panel, radar_svg, traffic_light_svg,
)
require_login()

st.set_page_config(page_title="個股分析卡", page_icon="🗂️", layout="wide")
st.title("🗂️ 個股深度分析卡")

if st.button("🔄 強制重新整理資料"):
    st.cache_data.clear()
    get_stock_list.cache_clear()
    st.rerun()

# ===== 股票搜尋（可輸入代號或公司名稱）=====
stock_list = get_stock_list()
options = (stock_list["stock_id"] + " " + stock_list["stock_name"].fillna("")).tolist() \
    if not stock_list.empty else []
# 從「每日選股訊號」頁跳過來時，預設選那檔股票；否則預設台積電
default_id = st.session_state.get("card_stock_id", "2330")
default_index = next((i for i, o in enumerate(options) if o.startswith(f"{default_id} ")), None)
selected = st.selectbox(
    "搜尋股票（輸入代號或公司名稱）", options, index=default_index,
    placeholder="例如: 2330 或 台積電",
)
if not selected:
    st.info("請選擇股票")
    st.stop()
stock_id = selected.split(" ", 1)[0]

# ===== 自選股 =====
watch_gid, watch_group = current_group()
if stock_id in set(get_watchlist(watch_gid)["stock_id"]):
    if st.button(f"★ 已在自選股「{watch_group}」（按一下移除）"):
        remove_from_watchlist(watch_gid, stock_id)
        st.rerun()
elif st.button(f"⭐ 加入自選股「{watch_group}」"):
    add_to_watchlist(watch_gid, stock_id)
    st.rerun()

# ===== 載入資料 =====
@st.cache_data(ttl=3600)
def load_card_data(stock_id):
    info        = get_stock_info(stock_id)
    prices      = get_daily_prices(stock_id, limit=250)
    quarterly   = get_quarterly(stock_id, limit=8)
    revenue     = get_revenue(stock_id, limit=12)
    ins         = get_stock_ins(stock_id, limit=250)  # 買賣超圖用 60 日、外資持股走勢最多 250 日，卡片取最後 10 日
    margin      = get_margin(stock_id, limit=1)
    holder      = get_holder(stock_id, limit=1)
    utilization = get_margin_utilization(stock_id, limit=60)  # ✅ 新增
    # 上櫃股票和櫃買指數比，其餘和加權指數比
    index_code  = "TPEX" if (info or {}).get("market") == "上櫃" else "TAIEX"
    index_df    = get_market_index(index_code, limit=250)
    return info, prices, quarterly, revenue, ins, margin, holder, utilization, index_df

info, prices, quarterly, revenue, ins, margin, holder, utilization, index_df = load_card_data(stock_id)

if not info:
    st.error(f"找不到股票代號：{stock_id}")
    st.stop()

if prices.empty:
    st.error("無股價資料")
    st.stop()

# ===== 計算數據 =====
prices_ta = calc_all(prices.copy())
signals   = get_latest_signals(prices_ta)

# 停牌或沒有成交的日子已在 calc_all 補成股價不變，並以 no_trade 標記
latest    = prices_ta.iloc[-1]
prev      = prices_ta.iloc[-2] if len(prices_ta) > 1 else latest
no_trade_days = prices_ta[prices_ta["no_trade"]]["date"].astype(str).tolist()
close     = latest["close_price"]
prev_close = prev["close_price"]
change    = close - prev_close
change_pct = (change / prev_close * 100) if prev_close else 0

high_52w  = prices["high_price"].max()
low_52w   = prices["low_price"].min()
position_pct = ((close - low_52w) / (high_52w - low_52w) * 100) if high_52w != low_52w else 50

latest_q      = quarterly.iloc[-1] if not quarterly.empty else {}
latest_rev    = revenue.iloc[-1] if not revenue.empty else {}
latest_margin = margin.iloc[-1] if not margin.empty else {}
latest_holder = holder.iloc[-1] if not holder.empty else {}
latest_util   = utilization.iloc[-1] if not utilization.empty else {}  # ✅ 新增

ins_10d      = ins.tail(10)
foreign_10d  = ins_10d["foreign_net_shares"].sum() if not ins.empty else 0
sitca_10d    = ins_10d["sitca_net_shares"].sum() if not ins.empty else 0
dealers_10d  = ins_10d["dealers_net_shares"].sum() if not ins.empty else 0

# 外資持股比率：最新值與近 20 個交易日的變化（百分點）
hold_ratio = pd.to_numeric(ins["foreign_hold_ratio"], errors="coerce").dropna() \
    if "foreign_hold_ratio" in ins.columns else pd.Series(dtype=float)
hold_latest = hold_ratio.iloc[-1] if not hold_ratio.empty else None
hold_chg_20 = hold_latest - hold_ratio.iloc[-21] if len(hold_ratio) > 20 else None

# 現金流量（千元，年初累計）→ 億元
def to_yi(v):
    return v / 1e5 if v is not None and pd.notna(v) else None

op_cf   = to_yi(latest_q.get("operating_cf"))
inv_cf  = to_yi(latest_q.get("investing_cf"))
free_cf = to_yi(latest_q.get("free_cf"))

# ===== 計算本益比 =====
# 季報的 EPS 是「年初至該季」的累計值，不是單季數字：
#   - 第 4 季的累計就是全年 EPS
#   - 有去年資料時算近四季：本期累計 + 去年全年 − 去年同期累計
#   - 否則用累計值年化：EPS × 4 ÷ 季別
pe_ratio = None
pe_note  = ""
q_label  = ""
if not quarterly.empty:
    q_sorted = quarterly.sort_values(["year", "quarter"])
    last_q   = q_sorted.iloc[-1]
    q_year   = int(last_q["year"])
    q_num    = int(last_q["quarter"])
    q_label  = f"{q_year} 年前 {q_num} 季累計"
    eps_now  = last_q.get("eps")

    annual_eps = None
    if eps_now is not None and pd.notna(eps_now) and q_num > 0:
        prev_full = q_sorted[(q_sorted["year"] == q_year - 1) & (q_sorted["quarter"] == 4)]["eps"].dropna()
        prev_same = q_sorted[(q_sorted["year"] == q_year - 1) & (q_sorted["quarter"] == q_num)]["eps"].dropna()
        if q_num == 4:
            annual_eps = eps_now
            pe_note    = "全年"
        elif not prev_full.empty and not prev_same.empty:
            annual_eps = eps_now + prev_full.iloc[-1] - prev_same.iloc[-1]
            pe_note    = "近4季"
        else:
            annual_eps = eps_now * 4 / q_num
            pe_note    = f"前{q_num}季年化"
    if annual_eps and annual_eps > 0 and close:
        pe_ratio = round(close / annual_eps, 2)

# ===== P/B 用最新收盤價現算（資料庫存的是抓季報當下的股價）=====
book_value = latest_q.get("book_value")
if book_value is not None and pd.notna(book_value) and book_value > 0 and close:
    pb_now = round(close / book_value, 2)
else:
    pb_now = latest_q.get("pb_ratio")

# ===== 葛蘭碧、每日評分、動能條件（儀表板與下方各區共用）=====
@st.cache_data(ttl=3600)
def load_granville(stock_id, prices, ins):
    cols = [c for c in ["stock_id", "date", "high_price", "low_price", "close_price", "volume"] if c in prices.columns]
    p = prices[cols].assign(stock_id=stock_id)
    i = ins[["date", "foreign_net_shares"]].assign(stock_id=stock_id) \
        if not ins.empty and "foreign_net_shares" in ins.columns else pd.DataFrame()
    return granville_status([stock_id], p, i)

@st.cache_data(ttl=3600)
def load_score_history(stock_id):
    return get_signal_history(stock_id, limit=60)

@st.cache_data(ttl=3600)
def load_momentum_row(stock_id):
    since = str(pd.Timestamp.today().date() - pd.Timedelta(days=20))
    df = get_rows_for_stocks("momentum_signals", [stock_id], since=since)
    return df.iloc[-1].to_dict() if not df.empty else None

g_df  = load_granville(stock_id, prices, ins)
g_now = g_df.iloc[0] if not g_df.empty else None
hist  = load_score_history(stock_id)
s_now = hist.iloc[-1] if not hist.empty else None
mrow  = load_momentum_row(stock_id)

def num(v):
    v = pd.to_numeric(v, errors="coerce")
    return None if v is None or pd.isna(v) else float(v)

def fmt(v, suffix="", decimals=2):
    if v is None or pd.isna(v):
        return "N/A"
    return f"{v:,.{decimals}f}{suffix}"

def lots(v, signed=True):
    """股數 → 張（四捨五入到整數，不顯示 -0）"""
    n = round(v / 1000)
    return f"{n:+,}" if signed and n else f"{n:,}"

up_color = UP_COLOR if change >= 0 else DOWN_COLOR
up_arrow = "▲" if change >= 0 else "▼"

rsi_val   = num(signals.get("RSI", {}).get("value"))
kd_k      = num(signals.get("KD", {}).get("K"))
kd_d      = num(signals.get("KD", {}).get("D"))
macd_hist = num(signals.get("MACD", {}).get("hist"))
ma20, ma60 = num(latest.get("MA20")), num(latest.get("MA60"))
ma20_prev = num(prices_ta["MA20"].iloc[-6]) if "MA20" in prices_ta.columns and len(prices_ta) > 5 else None
ma60_prev = num(prices_ta["MA60"].iloc[-6]) if "MA60" in prices_ta.columns and len(prices_ta) > 5 else None
bias20 = (close / ma20 - 1) * 100 if ma20 else None

# 量能：今天成交量和前 20 日均量的比值
vol = pd.to_numeric(prices_ta["volume"], errors="coerce")
vol20 = vol.iloc[-21:-1].mean() if len(vol) > 20 else None
vol_ratio = float(vol.iloc[-1] / vol20) if vol20 else None
volume_lots = vol.iloc[-1] / 1000 if pd.notna(vol.iloc[-1]) else None

# 波動：14 日 ATR 占股價的 %
hi_p = pd.to_numeric(prices_ta["high_price"], errors="coerce")
lo_p = pd.to_numeric(prices_ta["low_price"], errors="coerce")
cl_p = pd.to_numeric(prices_ta["close_price"], errors="coerce")
tr = pd.concat([hi_p - lo_p, (hi_p - cl_p.shift()).abs(), (lo_p - cl_p.shift()).abs()], axis=1).max(axis=1)
atr_pct = float(tr.tail(14).mean() / close * 100) if len(tr) >= 15 and close else None

# ---- ① 雷達圖：六個面向 0～10 分 ----
def score10(v, full):
    v = num(v)
    return None if v is None else round(min(max(v / full * 10, 0), 10), 1)

if mrow and num(mrow.get("trend_checks")) is not None and num(mrow.get("ma200")) is not None:
    trend10 = score10(mrow.get("trend_checks"), 8)
else:  # 動能資料不足一年時，改看收盤站上幾條均線
    mas = [num(latest.get(f"MA{n}")) for n in (5, 20, 60, 120, 240)]
    mas = [m for m in mas if m]
    trend10 = round(sum(close > m for m in mas) / len(mas) * 10, 1) if mas else None
rs10 = score10(mrow.get("rs_rating"), 100) if mrow else None
axes = [
    ("趨勢強度", trend10),
    ("相對強度", rs10),
    ("法人籌碼", score10(s_now["chip_score"], 32) if s_now is not None else None),
    ("資金面", score10(s_now["margin_score"], 8) if s_now is not None else None),
    ("基本面", score10(s_now["fund_score"], 35) if s_now is not None else None),
    ("技術面", score10(s_now["tech_score"], 25) if s_now is not None else None),
]
have = [v for _, v in axes if v is not None]
radar_avg = sum(have) / len(have) if len(have) >= 3 else None
grade, grade_desc = grade_of(radar_avg)

# 過熱風險：年區間位階、RSI、離 20 日線的乖離（10% 算滿分）平均
risk_parts = [position_pct, rsi_val, min(100, max(0, bias20) * 10) if bias20 is not None else None]
risk_parts = [p for p in risk_parts if p is not None]
risk = round(sum(risk_parts) / len(risk_parts)) if risk_parts else None

# ---- ② 紅綠燈 ----
g_state = str(g_now["狀態"]) if g_now is not None else ""
if g_state.startswith("⚠️"):
    light, light_why = "red", f"葛蘭碧{g_now['今天法則']}賣出訊號"
elif ma60 and ma60_prev and close < ma60 and ma60 < ma60_prev:
    light, light_why = "red", "跌破 60 日線，而且 60 日線往下彎"
elif rsi_val is not None and rsi_val > 75:
    light, light_why = "yellow", f"RSI {rsi_val:.0f}，短線過熱"
elif g_state.startswith("🔥"):
    light, light_why = "yellow", f"離 20 日線 {bias20:+.1f}%（葛蘭碧法則 8）"
elif position_pct > 90 and (bias20 or 0) > 10:
    light, light_why = "yellow", "接近一年高點，而且離均線太遠"
elif mrow and mrow.get("trend_template"):
    light, light_why = "green", "符合趨勢模板 8 個條件"
elif ma20 and ma60 and close > ma20 > ma60:
    light, light_why = "green", "站上 20、60 日線，均線多頭排列"
else:
    light, light_why = "gray", "趨勢不明或偏弱"
LIGHT_TEXT = {"red": "紅燈：出現賣出訊號", "yellow": "黃燈：過熱整理",
              "green": "綠燈：多頭趨勢", "gray": "灰燈：弱勢盤整"}
LIGHT_ADVICE = {"red": "留意風險，手上有的話考慮減碼或嚴守停損",
                "yellow": "強勢但過熱，不宜追高，控管部位",
                "green": "趨勢偏多，可搭配停損續抱",
                "gray": "方向不明，先觀望"}

# ---- ③ 多空能量 ----
def side(cond_bull, cond_bear):
    return 1 if cond_bull else -1 if cond_bear else 0

factors = []
if rsi_val is not None:
    factors.append(("RSI(14)", f"{rsi_val:.1f}", side(rsi_val > 55, rsi_val < 45)))
if macd_hist is not None:
    factors.append(("MACD 柱狀", f"{macd_hist:+.2f}", side(macd_hist > 0, macd_hist < 0)))
if kd_k is not None and kd_d is not None:
    factors.append(("KD", f"{kd_k:.0f}/{kd_d:.0f}", side(kd_k > kd_d, kd_k < kd_d)))
if ma20:
    factors.append(("20 日線", f"{bias20:+.1f}%", side(close > ma20, close < ma20)))
if ma20 and ma20_prev:
    factors.append(("20 日線方向", "上揚" if ma20 > ma20_prev else "下彎", side(ma20 > ma20_prev, ma20 < ma20_prev)))
if not ins.empty:
    factors.append(("外資 10 日", f"{lots(foreign_10d)} 張", side(foreign_10d > 0, foreign_10d < 0)))
    factors.append(("投信 10 日", f"{lots(sitca_10d)} 張", side(sitca_10d > 0, sitca_10d < 0)))
if vol_ratio is not None:
    pv = "價漲量增" if change > 0 and vol_ratio >= 1 else "價跌量增" if change < 0 and vol_ratio >= 1 else "量縮"
    factors.append(("量價", pv, side(pv == "價漲量增", pv == "價跌量增")))
n_bull = sum(1 for *_, s in factors if s > 0)
n_bear = sum(1 for *_, s in factors if s < 0)
bull_pct = round(n_bull / (n_bull + n_bear) * 100) if n_bull + n_bear else 50
energy_note = "多方能量較強" if bull_pct >= 65 else "空方能量較強" if bull_pct <= 35 else "多空拉鋸"
if light == "yellow" and bull_pct >= 65:
    energy_note += "，但已有過熱跡象"

# ---- ④ 健康儀表 ----
def lv_text(v, hi="強", mid="中等", lo="弱"):
    return "—" if v is None else hi if v >= 70 else mid if v >= 40 else lo

trend100 = trend10 * 10 if trend10 is not None else None
chip100 = axes[2][1] * 10 if axes[2][1] is not None else None
vol100 = min(100, vol_ratio / 2 * 100) if vol_ratio is not None else None
atr100 = min(100, atr_pct / 5 * 100) if atr_pct is not None else None
gauges = [
    ("趨勢健康度", trend100, lv_text(trend100)),
    ("籌碼健康度", chip100, lv_text(chip100)),
    ("量能", vol100, "—" if vol_ratio is None else
        f"{'爆量' if vol_ratio >= 1.5 else '正常' if vol_ratio >= 0.8 else '量縮'} {vol_ratio:.1f} 倍"),
    ("波動風險", atr100, "—" if atr_pct is None else f"{lv_text(atr100, '高', '中', '低')} {atr_pct:.1f}%"),
    ("股價位階", position_pct, lv_text(position_pct, "高檔", "中段", "低檔")),
]

stop_m = None
if mrow and num(mrow.get("atr14")) is not None:
    stop_m = stop_price(float(close), num(mrow.get("atr14")))

# ===== 組成儀表板 HTML =====
def kb(label, value, cls="", note=""):
    note_html = f'<div class="kn">{note}</div>' if note else ""
    return f'<div class="kb"><div class="kl">{label}</div><div class="kv {cls}">{value}</div>{note_html}</div>'

def sign_cls(v):
    return "kv-rd" if (v or 0) > 0 else "kv-gn" if (v or 0) < 0 else ""

p_radar = panel(1, "個股雷達圖", (
    f'<div style="text-align:center">{radar_svg(axes)}</div>'
    f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">'
    f'<div class="gbox"><div class="gl">個股等級</div><div class="gbig">{grade}</div>'
    f'<div class="gs" style="color:{ACCENT}">{grade_desc}</div></div>'
    f'<div class="gbox"><div class="gl">過熱風險</div>'
    f'<div class="gbig" style="color:{level_color(risk)};text-shadow:none">{risk if risk is not None else "—"}'
    f'<span style="font-size:16px;color:#94a3b8">/100</span></div>'
    f'<div class="gs" style="color:#94a3b8">位階・RSI・乖離</div></div></div>'
), sub="各項 0～10 分")

light_rows = "".join(
    f'<div class="lbl{" on" if key == light else ""}" style="{f"color:{color};border-color:{color}" if key == light else ""}">'
    f'{"▶ " if key == light else ""}{LIGHT_TEXT[key]}</div>' for key, color in LIGHTS)
p_light = panel(2, "預警燈號系統", (
    f'<div class="lights">{traffic_light_svg(light)}<div class="lrow">{light_rows}</div></div>'
    f'<div class="verdict">目前狀態：<b style="color:{dict(LIGHTS)[light]}">{LIGHT_TEXT[light].split("：")[1]}</b>'
    f'　{light_why}</div>'
))

bull_blk, bear_blk = energy_blocks(bull_pct)
energy_rows = "".join(
    f'<div><span>{name}</span><span>{val}　<b style="color:{UP_COLOR if s > 0 else DOWN_COLOR if s < 0 else "#94a3b8"}">'
    f'{"偏多" if s > 0 else "偏空" if s < 0 else "中性"}</b></span></div>' for name, val, s in factors)
p_energy = panel(3, "多空能量條", (
    f'<div class="ebar"><div><div class="gl">多方能量</div><div class="epct" style="color:{UP_COLOR}">{bull_pct}%</div></div>'
    f'<div class="eblk">{bull_blk}</div><b style="color:#e2e8f0">VS</b><div class="eblk">{bear_blk}</div>'
    f'<div style="text-align:right"><div class="gl">空方能量</div>'
    f'<div class="epct" style="color:{DOWN_COLOR}">{100 - bull_pct}%</div></div></div>'
    f'<div class="etab">{energy_rows}</div>'
    f'<div class="verdict" style="color:{UP_COLOR if bull_pct >= 65 else DOWN_COLOR if bull_pct <= 35 else ACCENT}">'
    f'結論：{energy_note}</div>'
), sub=f"{n_bull} 多 {n_bear} 空")

gauge_html = "".join(
    f'<div><div class="gl">{label}</div>{gauge_svg(v)}<div class="gs" style="color:{level_color(v)}">{t}</div></div>'
    for label, v, t in gauges)
ranked = sorted(((v, n) for n, v in axes if v is not None), reverse=True)
strong = "、".join(f"{n} {v:.1f}" for v, n in ranked[:2] if v >= 6) or "無"
weak = "、".join(f"{n} {v:.1f}" for v, n in ranked[::-1][:2] if v < 5) or "無"
p_gauge = panel(4, "個股健康儀表板", (
    f'<div class="gauges">{gauge_html}</div>'
    f'<div class="verdict">雷達強項：<b style="color:{UP_COLOR}">{strong}</b>'
    f'　弱項：<b style="color:{DOWN_COLOR}">{weak}</b>　（6 分以上算強、5 分以下算弱）</div>'
), cls="span2",
                sub="量能＝今天成交量 ÷ 前 20 日均量；波動＝14 日平均真實波幅占股價 %")

p_chip = panel(5, "52 週區間與法人籌碼", (
    f'<div class="rl2"><span class="lo">低 {fmt(low_52w)}</span><span class="mi">目前 {fmt(close)}</span>'
    f'<span class="hi">高 {fmt(high_52w)}</span></div><div class="rt"><div class="rth"></div></div>'
    f'<div class="rm2"><span>位於年區間 <strong>{fmt(position_pct, "%", 1)}</strong></span></div>'
    f'<div class="kg" style="grid-template-columns:repeat(3,1fr);margin-top:12px;">'
    + kb("外資 10 日", lots(foreign_10d, False), sign_cls(foreign_10d), "張")
    + kb("投信 10 日", lots(sitca_10d, False), sign_cls(sitca_10d), "張")
    + kb("自營商 10 日", lots(dealers_10d, False), sign_cls(dealers_10d), "張")
    + kb("大戶持股", fmt(latest_holder.get("big_holder_ratio")), "kv-or", "%")
    + kb("外資持股", fmt(hold_latest), "kv-or",
         f'%・20 日 <span style="color:{UP_COLOR if (hold_chg_20 or 0) > 0 else DOWN_COLOR};font-weight:700">'
         f'{f"{hold_chg_20:+.2f}" if hold_chg_20 is not None else "N/A"}</span>')
    + kb("融資使用率", fmt(latest_util.get("margin_rate"), "%") if latest_util.get("margin_rate") is not None else "N/A", "kv-or")
    + "</div>"
))

p_fund = panel(6, f"基本面（{q_label or '最新季'}）", (
    '<div class="kg5">'
    + kb("EPS（年初累計）", fmt(latest_q.get("eps")), sign_cls(latest_q.get("eps")), "元")
    + kb(f"本益比（{pe_note}）", fmt(pe_ratio) if pe_ratio else "N/A", "kv-or", "倍")
    + kb("ROE（年初累計）", fmt(latest_q.get("roe")), "kv-or", "%")
    + kb("股價淨值比", fmt(pb_now), "kv-or", "倍")
    + kb("每股淨值", fmt(latest_q.get("book_value")), "", "元")
    + '</div><div class="kg" style="grid-template-columns:repeat(3,1fr);margin-top:10px;">'
    + kb("營業現金流", fmt(op_cf, "", 1), sign_cls(op_cf), "億元（年初累計）")
    + kb("投資現金流", fmt(inv_cf, "", 1), "", "億元（負值代表投入）")
    + kb("自由現金流", fmt(free_cf, "", 1), sign_cls(free_cf), "億元")
    + "</div>"
), cls="span2")

rev_val = latest_rev.get("revenue")
p_rev = panel(7, "營收與融資融券", (
    '<div class="kg" style="grid-template-columns:repeat(2,1fr);">'
    + kb(f"月營收（{latest_rev.get('year_month', 'N/A')}）", fmt(rev_val / 1000, "", 1) if rev_val else "N/A", "", "百萬元")
    + kb("年增率", fmt(latest_rev.get("yoy_growth"), "%"), sign_cls(latest_rev.get("yoy_growth")))
    + kb("月增率", fmt(latest_rev.get("mom_growth"), "%"), sign_cls(latest_rev.get("mom_growth")))
    + kb("融資餘額", f"{latest_margin.get('margin_balance'):,.0f}" if latest_margin.get("margin_balance") else "N/A", "", "張")
    + kb("融券餘額", f"{latest_margin.get('short_balance'):,.0f}" if latest_margin.get("short_balance") else "N/A", "", "張")
    + kb("融券使用率", fmt(latest_util.get("short_rate"), "%") if latest_util.get("short_rate") is not None else "N/A", "kv-or")
    + "</div>"
))

stop_text = f"｜停損參考 {stop_m:,.2f}（{STOP_RULE.replace('預計買進價', '收盤價')}）" if stop_m else ""
summary = (f'總評：<b style="color:{ACCENT}">{grade} {grade_desc}</b>｜'
           f'<b style="color:{dict(LIGHTS)[light]}">{LIGHT_TEXT[light]}</b>｜多方能量 {bull_pct}%｜'
           f'{LIGHT_ADVICE[light]}{stop_text}')

html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700;900&family=IBM+Plex+Mono:wght@400;600;700&display=swap');
:root{{
  --bdr:#1e3a6e;--inn:rgba(5,11,26,.55);
  --th:#f8fafc;--tp:#e2e8f0;--ts:#94a3b8;--td:#7c8db0;
  --gn:{DOWN_COLOR};--rd:{UP_COLOR};--or:{ACCENT};
}}
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:transparent;font-family:"Noto Sans TC","IBM Plex Mono",sans-serif;color:var(--tp);}}
.wrap{{padding:4px 2px 8px;}}
.hd{{display:flex;flex-wrap:wrap;align-items:center;gap:10px 22px;padding:12px 16px;margin-bottom:12px;
     border:1px solid var(--bdr);border-radius:8px;background:linear-gradient(90deg,#0d1b3a,#102a5c 60%,#0d1b3a);
     box-shadow:0 0 14px rgba(56,189,248,.2);}}
.hid{{font-family:"IBM Plex Mono",monospace;font-size:26px;font-weight:700;color:{ACCENT};text-shadow:0 0 10px rgba(251,191,36,.5);}}
.hname{{font-size:26px;font-weight:900;color:var(--th);}}
.htag{{font-size:12px;color:var(--ts);border:1px solid var(--bdr);border-radius:20px;padding:2px 10px;}}
.hbox{{border:1px solid var(--bdr);border-radius:6px;padding:4px 12px;background:var(--inn);font-size:13px;color:var(--ts);}}
.hbox b{{font-family:"IBM Plex Mono",monospace;font-size:20px;}}
.hdate{{margin-left:auto;font-size:12px;color:var(--ts);}}
.rl2{{display:flex;justify-content:space-between;font-family:"IBM Plex Mono",monospace;font-size:12px;margin-bottom:7px;}}
.lo{{color:var(--gn);font-weight:700;}} .mi{{color:var(--ts);}} .hi{{color:var(--rd);font-weight:700;}}
.rt{{position:relative;height:10px;background:linear-gradient(90deg,rgba(22,163,74,.45),rgba(251,191,36,.35),rgba(220,38,38,.5));border-radius:5px;}}
.rth{{position:absolute;top:50%;transform:translate(-50%,-50%);width:14px;height:14px;background:{ACCENT};border-radius:50%;
      box-shadow:0 0 0 2px #050b1a,0 0 10px {ACCENT};left:{position_pct}%;}}
.rm2{{margin-top:7px;font-size:11.5px;color:var(--ts);}}
.kg{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;}}
.kg5{{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;}}
@media(max-width:700px){{.kg5{{grid-template-columns:repeat(2,1fr);}}}}
.kb{{background:var(--inn);border:1px solid var(--bdr);border-radius:6px;padding:9px 11px;}}
.kl{{font-size:11px;color:var(--ts);margin-bottom:4px;}}
.kv{{font-family:"IBM Plex Mono",monospace;font-size:19px;font-weight:700;line-height:1.1;color:var(--th);}}
.kv-gn{{color:var(--gn);}} .kv-or{{color:var(--or);}} .kv-rd{{color:var(--rd);}}
.kn{{font-size:10.5px;color:var(--td);margin-top:3px;}}
.sum{{margin-top:12px;padding:10px 14px;border:1px solid {ACCENT};border-radius:8px;font-size:14px;text-align:center;
      background:rgba(251,191,36,.06);box-shadow:0 0 12px rgba(251,191,36,.15);}}
.cf{{margin-top:10px;font-size:11px;color:var(--td);line-height:1.6;text-align:center;}}
{DASH_CSS}
</style>
</head>
<body>
<div class="wrap">
<div class="hd">
  <span class="hid">{stock_id}</span><span class="hname">{info.get('stock_name', '')}</span>
  <span class="htag">{info.get('market', '')}・{info.get('industry', '')}</span>
  <span class="hbox">收盤 <b style="color:{up_color}">{fmt(close)}</b>
    <span style="color:{up_color};font-weight:700">{up_arrow} {fmt(abs(change))}（{fmt(change_pct, '%')}）</span></span>
  <span class="hbox">成交量 <b style="color:{ACCENT}">{f"{volume_lots:,.0f}" if volume_lots is not None else "N/A"}</b> 張</span>
  <span class="hdate">資料日期：{latest['date']}</span>
</div>
<div class="dash">
{p_radar}{p_light}{p_energy}
{p_gauge}{p_chip}
{p_fund}{p_rev}
</div>
<div class="sum">{summary}</div>
<div class="cf">
  雷達圖用每日選股評分（法人 32、資金 8、基本面 35、技術 25 分換算成 10 分）與動能條件（趨勢模板 8 條件、相對強度）；
  燈號與能量條依均線、RSI、KD、MACD、法人與量價用固定規則判斷，不是預測。<br>
  資料來源：證交所、櫃買中心、集保結算所、公開資訊觀測站｜資料截至 {latest['date']}｜僅供參考，不構成投資建議，投資請自負風險。
</div>
</div>
</body>
</html>
"""

# st.components.v1.html 已被 Streamlit 標為淘汰，改用 st.iframe；高度跟著內容自動調整，不必另外捲動
st.iframe(html, height="content")

# ===== 葛蘭碧與每日選股分析（和自選股頁相同的判斷）=====
SCORE_RECENT, SCORE_TOP = 20, 20
st.markdown("---")
g_col, s_col = st.columns(2)
with g_col:
    st.subheader("📐 葛蘭碧分析")
    g = load_granville(stock_id, prices, ins)
    if g.empty:
        st.info("股價資料不足，暫時無法判斷葛蘭碧法則（要有 20 個交易日以上的資料）。")
    else:
        g = g.iloc[0]
        render_metric_cards([
            ("狀態", g["狀態"], g["今天法則"] or "今天沒有符合的法則", None),
            ("20 日均線", f"{g['均線']:,.2f}", f"均線{g['均線方向']}", None),
            ("乖離", f"{g['乖離%']:+.2f}%", "收盤價離均線", g["乖離%"]),
        ])
        st.markdown(f"💬 {g['說明']}")
        lines = []
        if pd.notna(g["買進觸發價"]):
            lines.append(f"- **買進觸發價**：明天收盤 ≥ {g['買進觸發價']:,.2f} 元就出現法則 2")
        if pd.notna(g["停損價"]):
            lines.append(f"- **停損價**：{g['停損價']:,.2f} 元（{STOP_RULE}），一張最多約虧 {g['每張風險']:,.0f} 元")
        if pd.notna(g["賣出觸發價"]):
            lines.append(f"- **賣出觸發價**：明天收盤 ≤ {g['賣出觸發價']:,.2f} 元（{g['賣出距離%']:+.2f}%）會出現法則 5 或 6")
        lines.append(f"- **趨勢**：{g['趨勢']}")
        st.markdown("\n".join(lines))
        st.caption("依 20 日均線、葛蘭碧頁的預設參數；觸發價是推算「下一個交易日收盤」要到多少才會出現訊號。")
    try:
        st.page_link("pages/6_葛蘭碧八大法則.py", label="到葛蘭碧頁看完整說明", icon="📐")
    except Exception:  # 單獨執行這一頁時找不到其他頁面
        pass

with s_col:
    st.subheader("🎯 每日選股評分")
    hist = load_score_history(stock_id)
    if hist.empty:
        st.info("這檔股票沒有每日選股評分（可能是新上市或資料不足）。")
    else:
        now = hist.iloc[-1]
        recent = hist.tail(SCORE_RECENT)
        ranks = pd.to_numeric(recent["rank"], errors="coerce")
        prev_score = hist.iloc[-2]["total_score"] if len(hist) > 1 else None
        diff = now["total_score"] - prev_score if prev_score is not None else None
        render_metric_cards([
            ("總分", f"{int(now['total_score'])} / 100",
             f"較前一日 {diff:+.0f}" if diff is not None else now["date"], diff),
            ("排名", f"第 {int(now['rank'])} 名" if pd.notna(now["rank"]) else "未列入排名",
             "只排 20 日均量 500 張以上", None),
            (f"近 {SCORE_RECENT} 日", f"平均 {pd.to_numeric(recent['total_score'], errors='coerce').mean():.1f} 分",
             f"進前 {SCORE_TOP} 名 {int((ranks <= SCORE_TOP).sum())} 天", None),
        ])
        st.markdown(f"籌碼 {int(now['chip_score'])}/32、資金 {int(now['margin_score'])}/8、"
                    f"基本面 {int(now['fund_score'])}/35、技術 {int(now['tech_score'])}/25（評分日 {now['date']}）")
        reasons = now["reasons"] if isinstance(now["reasons"], list) else []
        warnings = now["warnings"] if isinstance(now["warnings"], list) else []
        st.markdown("**✅ 加分理由**：" + ("、".join(reasons) or "無"))
        if warnings:
            st.markdown("**⚠️ 風險提醒**：" + "、".join(warnings))
        trend = hist.assign(date=pd.to_datetime(hist["date"]),
                            total_score=pd.to_numeric(hist["total_score"], errors="coerce"))
        st.altair_chart(alt.Chart(trend).mark_line(point=alt.OverlayMarkDef(size=15), color=UP_COLOR).encode(
            x=alt.X("date:T", title=None, axis=alt.Axis(format="%m/%d")),
            y=alt.Y("total_score:Q", title="總分", scale=alt.Scale(domain=[0, 100])),
            tooltip=[alt.Tooltip("date:T", title="日期"), alt.Tooltip("total_score:Q", title="總分")],
        ).properties(height=180), width="stretch")
    try:
        st.page_link("pages/7_每日選股訊號.py", label="到每日選股頁看規則回測", icon="🎯")
    except Exception:
        pass

# ===== 動能條件：趨勢模板、52 週新高突破（momentum.py 每天收盤後計算）=====
st.markdown("---")
st.subheader("🚀 動能條件（趨勢模板＋52 週新高）")
mrow = load_momentum_row(stock_id)
if mrow is None:
    st.info("還沒有這檔股票的動能資料（每天收盤後計算，新上市不滿一年的股票沒有 52 週資料）。")
else:
    conds = conditions(mrow)
    n_ok = sum(1 for ok, _, _ in conds if ok)
    liquid_m = bool(mrow.get("liquid"))
    if not liquid_m:
        tt_text, tt_note = f"{n_ok}/8", "20 日均量不到 500 張，不列入判斷"
    elif mrow.get("trend_template"):
        tt_text, tt_note = "✅ 符合", "今天剛符合" if mrow.get("trend_new") else "8 個條件全部符合"
    else:
        tt_text, tt_note = f"{n_ok}/8", "還差 " + str(8 - n_ok) + " 個條件"
    hi = pd.to_numeric(mrow.get("high_52w"), errors="coerce")
    c_m = pd.to_numeric(mrow.get("close_price"), errors="coerce")
    vr = pd.to_numeric(mrow.get("vol_ratio"), errors="coerce")
    if mrow.get("breakout"):
        bo_text, bo_note = "✅ 今天突破", f"量比 {vr:.2f} 倍"
    elif mrow.get("new_high"):
        bo_text, bo_note = "創新高", f"量比 {vr:.2f} 倍，" + ("10 日內已突破過" if vr >= 1.5 else "量不夠（要 1.5 倍）")
    else:
        bo_text = f"{(c_m / hi - 1) * 100:+.1f}%" if pd.notna(hi) and hi > 0 else "—"
        bo_note = f"離 52 週高點 {hi:,.2f}" if pd.notna(hi) else "資料不滿一年"
    rs_m = mrow.get("rs_rating")
    stop_m = stop_price(float(c_m), pd.to_numeric(mrow.get("atr14"), errors="coerce")) if pd.notna(c_m) else None
    render_metric_cards([
        ("趨勢模板", tt_text, tt_note, None),
        ("52 週新高突破", bo_text, bo_note, None),
        ("相對強度 RS", f"{int(rs_m)}" if rs_m is not None and pd.notna(rs_m) else "—", "0～99，贏過多少 % 的股票", None),
        ("停損價（以收盤買進）", f"{stop_m:,.2f}" if stop_m else "—",
         f"一張最多約虧 {(c_m - stop_m) * 1000:,.0f} 元" if stop_m else "", None),
    ])
    left, right = st.columns(2)
    for k, (ok, label, detail) in enumerate(conds):
        icon = "✅" if ok else "—" if ok is None else "❌"
        (left if k < 4 else right).markdown(f"{icon} **{label}**：{detail}")
    st.caption(f"資料日期：{mrow.get('date')}。停損價是{STOP_RULE.replace('預計買進價', '收盤價')}。")
    with st.expander("📖 條件說明與回測結果"):
        st.markdown(RULES_MD)
        st.markdown(BACKTEST_MD)
    try:
        st.page_link("pages/7_每日選股訊號.py", label="到每日選股頁看今天符合條件的股票", icon="🚀")
    except Exception:
        pass

# ===== 圖表 =====
st.markdown("---")
st.subheader("📈 股價走勢")
if latest["no_trade"]:
    last_traded = prices[pd.to_numeric(prices["close_price"], errors="coerce").notna()]["date"]
    since = f"，最後成交日 {last_traded.iloc[-1]}" if not last_traded.empty else ""
    st.warning(f"⏸️ {latest['date']} 沒有成交（停牌或當天無人交易）{since}，價格沿用最後成交價。")
if no_trade_days:
    st.caption(f"⏸️ 近 {len(prices_ta)} 個交易日中有 {len(no_trade_days)} 天沒有成交（停牌或無人交易），"
               "圖上以灰色標示，這些天視為股價不變。")
st.caption("股價已還原權值：除權息、減資、變更面額之前的價格依參考價等比例調整，均線與各項指標不會因此出現假跳空；最新股價不受影響。")
range_days = st.radio(
    "顯示區間", [60, 120, 250], index=1, horizontal=True,
    format_func=lambda d: f"近 {d} 個交易日",
)

chart_df = prices_ta.tail(range_days).copy()
chart_df["date"] = chart_df["date"].astype(str)
chart_df["漲跌"] = np.select(
    [chart_df["no_trade"], chart_df["close_price"] >= chart_df["open_price"]], ["無成交", "漲"], "跌")

# 日期用類別軸，週末與休市日不會留空；標籤大約顯示 6 個
step = max(1, len(chart_df) // 6)
x_axis = alt.X(
    "date:O", title=None,
    axis=alt.Axis(values=chart_df["date"].iloc[::step].tolist(), labelAngle=0),
)
up_down = alt.Color(
    "漲跌:N", scale=alt.Scale(domain=["漲", "跌", "無成交"], range=[UP_COLOR, DOWN_COLOR, "#9ca3af"]),
    legend=None,
)
price_tooltip = [
    alt.Tooltip("date:N", title="日期"),
    alt.Tooltip("漲跌:N", title="狀態"),
    alt.Tooltip("open_price:Q", title="開盤", format=",.2f"),
    alt.Tooltip("high_price:Q", title="最高", format=",.2f"),
    alt.Tooltip("low_price:Q", title="最低", format=",.2f"),
    alt.Tooltip("close_price:Q", title="收盤", format=",.2f"),
    alt.Tooltip("volume:Q", title="成交量", format=","),
]

base = alt.Chart(chart_df).encode(x=x_axis)
wick = base.mark_rule(strokeWidth=1).encode(
    y=alt.Y("low_price:Q", title="股價", scale=alt.Scale(zero=False)),
    y2="high_price:Q", color=up_down, tooltip=price_tooltip,
)
body = base.mark_bar(size=max(2, int(600 / len(chart_df)))).encode(
    y="open_price:Q", y2="close_price:Q", color=up_down, tooltip=price_tooltip,
)

ma_cols = [c for c in ["MA5", "MA20", "MA60"] if c in chart_df.columns and chart_df[c].notna().any()]
# 沒有成交的日子開高低收相同，K 棒看不到，改畫灰色菱形標記
no_trade_mark = base.transform_filter("datum['漲跌'] == '無成交'").mark_point(
    shape="diamond", filled=True, size=40, color="#9ca3af", opacity=1,
).encode(y="close_price:Q", tooltip=price_tooltip)
layers = [wick, body, no_trade_mark]
if ma_cols:
    ma_lines = base.transform_fold(ma_cols, as_=["均線", "值"]).mark_line(strokeWidth=2).encode(
        y="值:Q",
        color=alt.Color(
            "均線:N",
            scale=alt.Scale(domain=["MA5", "MA20", "MA60"], range=[ACCENT, GLOW, "#a78bfa"]),
            legend=alt.Legend(orient="top", title=None),
        ),
        tooltip=[alt.Tooltip("date:N", title="日期"), alt.Tooltip("均線:N"),
                 alt.Tooltip("值:Q", title="數值", format=",.2f")],
    )
    layers.append(ma_lines)

price_chart = alt.layer(*layers).resolve_scale(color="independent").properties(height=320)
volume_chart = base.mark_bar(size=max(2, int(600 / len(chart_df)))).encode(
    y=alt.Y("volume:Q", title="成交量", axis=alt.Axis(format="~s")),
    color=up_down, tooltip=price_tooltip,
).properties(height=110)

# 分成兩張圖各自撐滿寬度（vconcat 無法自動配合頁面寬度）；兩張用同一份資料，日期軸會對齊
st.altair_chart(price_chart, width="stretch")
st.altair_chart(volume_chart, width="stretch")

# ===== 相對大盤強弱 =====
index_name = "櫃買指數" if info.get("market") == "上櫃" else "加權指數"
st.subheader(f"📊 相對大盤強弱（與{index_name}比較，近 {range_days} 個交易日）")
if index_df.empty:
    st.info("無大盤指數資料")
else:
    rel = chart_df[["date", "close_price"]].merge(
        index_df.assign(date=index_df["date"].astype(str))[["date", "close"]], on="date", how="inner")
    rel = rel.dropna()
    if len(rel) < 2:
        st.info("可比較的交易日不足")
    else:
        rel["個股"] = rel["close_price"] / rel["close_price"].iloc[0] * 100
        rel[index_name] = pd.to_numeric(rel["close"]) / float(rel["close"].iloc[0]) * 100
        stock_ret = rel["個股"].iloc[-1] - 100
        index_ret = rel[index_name].iloc[-1] - 100
        excess = stock_ret - index_ret
        color = UP_COLOR if excess >= 0 else DOWN_COLOR
        st.markdown(
            f"區間漲跌：個股 **{stock_ret:+.2f}%**、{index_name} **{index_ret:+.2f}%**，"
            f"相對大盤 <span style='color:{color};font-weight:700;'>{excess:+.2f} 個百分點</span>"
            f"（{'強於' if excess >= 0 else '弱於'}大盤）",
            unsafe_allow_html=True,
        )
        rel_long = rel[["date", "個股", index_name]].melt("date", var_name="對象", value_name="指數化")
        rel_step = max(1, len(rel) // 6)
        rel_chart = alt.Chart(rel_long).mark_line(strokeWidth=2).encode(
            x=alt.X("date:O", title=None,
                    axis=alt.Axis(values=rel["date"].iloc[::rel_step].tolist(), labelAngle=0)),
            y=alt.Y("指數化:Q", title="起點＝100", scale=alt.Scale(zero=False)),
            color=alt.Color("對象:N", scale=alt.Scale(domain=["個股", index_name], range=[ACCENT, "#9ca3af"]),
                            legend=alt.Legend(orient="top", title=None)),
            tooltip=[alt.Tooltip("date:N", title="日期"), alt.Tooltip("對象:N"),
                     alt.Tooltip("指數化:Q", title="起點＝100", format=",.2f")],
        ).properties(height=240)
        st.altair_chart(rel_chart, width="stretch")
        st.caption("兩條線都以區間第一天為 100，個股用還原權值後的股價。金線在灰線上方代表這段期間漲得比大盤多。")

st.subheader("🏦 三大法人買賣超（近 60 個交易日）")
if ins.empty:
    st.info("無法人買賣超資料")
else:
    ins_df = ins.tail(60)[["date", "foreign_net_shares", "sitca_net_shares", "dealers_net_shares"]].copy()
    ins_df["date"] = ins_df["date"].astype(str)
    ins_df = ins_df.rename(columns={
        "foreign_net_shares": "外資", "sitca_net_shares": "投信", "dealers_net_shares": "自營商",
    })
    ins_long = ins_df.melt("date", var_name="法人", value_name="買賣超")
    ins_step = max(1, len(ins_df) // 6)
    ins_chart = alt.Chart(ins_long).mark_bar().encode(
        x=alt.X("date:O", title=None,
                axis=alt.Axis(values=ins_df["date"].iloc[::ins_step].tolist(), labelAngle=0)),
        xOffset=alt.XOffset("法人:N", sort=["外資", "投信", "自營商"]),
        y=alt.Y("買賣超:Q", title="買賣超（股）", axis=alt.Axis(format="~s")),
        color=alt.Color(
            "法人:N",
            scale=alt.Scale(domain=["外資", "投信", "自營商"], range=[ACCENT, GLOW, "#a78bfa"]),
            legend=alt.Legend(orient="top", title=None),
        ),
        tooltip=[alt.Tooltip("date:N", title="日期"), alt.Tooltip("法人:N"),
                 alt.Tooltip("買賣超:Q", title="買賣超（股）", format=",")],
    ).properties(height=260)
    st.altair_chart(ins_chart, width="stretch")

st.subheader(f"🌐 外資持股比率（近 {range_days} 個交易日）")
if hold_ratio.empty:
    st.info("無外資持股資料")
else:
    hold_df = ins[["date", "foreign_hold_ratio"]].dropna().tail(range_days).copy()
    hold_df["date"] = hold_df["date"].astype(str)
    hold_df["foreign_hold_ratio"] = pd.to_numeric(hold_df["foreign_hold_ratio"], errors="coerce")
    hold_step = max(1, len(hold_df) // 6)
    hold_chart = alt.Chart(hold_df).mark_line(strokeWidth=2, color=ACCENT).encode(
        x=alt.X("date:O", title=None,
                axis=alt.Axis(values=hold_df["date"].iloc[::hold_step].tolist(), labelAngle=0)),
        y=alt.Y("foreign_hold_ratio:Q", title="外資持股比率（%）", scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("date:N", title="日期"),
                 alt.Tooltip("foreign_hold_ratio:Q", title="外資持股比率（%）", format=".2f")],
    ).properties(height=220)
    st.altair_chart(hold_chart, width="stretch")
    first, last = hold_df["foreign_hold_ratio"].iloc[0], hold_df["foreign_hold_ratio"].iloc[-1]
    st.caption(f"區間內由 {first:.2f}% 變成 {last:.2f}%（{last - first:+.2f} 個百分點）。"
               "外資持股是外資及陸資實際持有的股數占發行股數的比率，和每天的買賣超不同，看的是長期累積。")
