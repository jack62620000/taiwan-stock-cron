import streamlit as st
import pandas as pd
import numpy as np
import altair as alt
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.db import (
    get_stock_list,
    get_watchlist, add_to_watchlist, remove_from_watchlist, get_signal_history, get_rows_for_stocks,
)
from utils.analysis import granville_status
from utils.card import card_metrics, load_card_data as _load_card_data
from utils.granville import STOP_RULE
from utils.momentum import BACKTEST_MD, RULES_MD, momentum_summary
from utils.groups import current_group
from utils.ui import require_login, render_metric_cards, UP_COLOR, DOWN_COLOR, ACCENT, GLOW
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
    return _load_card_data(stock_id)

info, prices, quarterly, revenue, ins, margin, holder, utilization, index_df = load_card_data(stock_id)

if not info:
    st.error(f"找不到股票代號：{stock_id}")
    st.stop()

if prices.empty:
    st.error("無股價資料")
    st.stop()

# ===== 計算數據 =====
m = card_metrics(prices, quarterly, revenue, ins, margin, holder, utilization)
prices_ta, signals, latest, prev, no_trade_days, close = (m["prices_ta"], m["signals"], m["latest"], m["prev"], m["no_trade_days"], m["close"])
prev_close, change, change_pct, high_52w, low_52w, position_pct = (m["prev_close"], m["change"], m["change_pct"], m["high_52w"], m["low_52w"], m["position_pct"])
latest_q, latest_rev, latest_margin, latest_holder, latest_util, foreign_10d = (m["latest_q"], m["latest_rev"], m["latest_margin"], m["latest_holder"], m["latest_util"], m["foreign_10d"])
sitca_10d, dealers_10d, hold_latest, hold_chg_20, op_cf, inv_cf = (m["sitca_10d"], m["dealers_10d"], m["hold_latest"], m["hold_chg_20"], m["op_cf"], m["inv_cf"])
free_cf, pe_ratio, pe_note, q_label, book_value, pb_now = (m["free_cf"], m["pe_ratio"], m["pe_note"], m["q_label"], m["book_value"], m["pb_now"])
rsi_val, rsi_status, kd_k, kd_d, kd_status, macd_hist = (m["rsi_val"], m["rsi_status"], m["kd_k"], m["kd_d"], m["kd_status"], m["macd_hist"])
ma5, ma20, ma60, vol_ratio, extra_tech, hold_ratio = (m["ma5"], m["ma20"], m["ma60"], m["vol_ratio"], m["extra_tech"], m["hold_ratio"])

up_color = UP_COLOR if change >= 0 else DOWN_COLOR
up_arrow = "▲" if change >= 0 else "▼"

extra_tech_html = "".join(
    f'<div class="tb"><div class="tlb">{label}</div><div class="tbdg {cls}">{value}</div>'
    f'<div class="tdd">{note}</div></div>' for label, value, cls, note in extra_tech)

def fmt(v, suffix="", decimals=2):
    if v is None or pd.isna(v):
        return "N/A"
    return f"{v:.{decimals}f}{suffix}"

# ===== 建立 HTML 卡片 =====
html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700;900&family=IBM+Plex+Mono:wght@400;600;700&display=swap');
:root{{
  --bg:#050b1a;--card:#0b1631;--inn:rgba(5,11,26,.55);
  --bdr:#1e3a6e;--bdr2:#2b4f8f;--glow:#38bdf8;
  --th:#f8fafc;--tp:#e2e8f0;--ts:#94a3b8;--td:#8fa0c2;
  --gn:#16a34a;--gbg:rgba(22,163,74,.15);--gbr:rgba(22,163,74,.45);--gdk:#86efac;
  --or:#fbbf24;--obg:rgba(251,191,36,.12);--obr:rgba(251,191,36,.45);--odk:#fde68a;
  --rd:#dc2626;--rbg:rgba(220,38,38,.15);--rbr:rgba(220,38,38,.45);--rdk:#fca5a5;
  --am:#fbbf24;--ambg:rgba(251,191,36,.12);--ambr:rgba(251,191,36,.45);
  --sh:0 0 0 1px rgba(56,189,248,.08),0 0 14px rgba(56,189,248,.18) inset;
}}
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:transparent;font-family:"IBM Plex Mono","Noto Sans TC",monospace;color:var(--tp);}}
.wrap{{max-width:1100px;margin:0 auto;padding:4px 2px 8px;}}
.card{{background:transparent;}}
.stripe{{display:none;}}
.ch{{padding:18px 22px 16px;border:1px solid var(--bdr);border-radius:8px;
     background:linear-gradient(90deg,#0d1b3a,#102a5c 60%,#0d1b3a);box-shadow:0 0 14px rgba(56,189,248,.22);}}
.hrow{{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;}}
.stag{{font-family:"IBM Plex Mono",monospace;font-size:11px;font-weight:700;letter-spacing:2px;color:var(--or);background:var(--obg);border:1px solid var(--obr);padding:3px 9px;border-radius:4px;display:inline-block;margin-bottom:6px;}}
.sname{{font-size:32px;font-weight:900;color:var(--th);line-height:1;text-shadow:0 0 10px rgba(56,189,248,.35);}}
.sectag{{display:inline-flex;align-items:center;gap:5px;font-size:12px;color:var(--ts);background:var(--inn);border:1px solid var(--bdr);border-radius:20px;padding:4px 12px;margin-top:8px;}}
.prow{{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;justify-content:flex-end;}}
.pnum{{font-family:"IBM Plex Mono",monospace;font-size:44px;font-weight:700;color:var(--th);line-height:1;}}
.pt{{font-family:"IBM Plex Mono",monospace;font-size:14px;font-weight:700;padding:3px 9px;border-radius:6px;color:{up_color};background:{('rgba(220,38,38,.15)' if change >= 0 else 'rgba(22,163,74,.15)')};}}
.pdt{{font-size:12.5px;color:var(--td);align-self:flex-end;padding-bottom:5px;}}
.cb{{padding:0;}}
.sec{{margin-top:12px;padding:14px 16px 16px;border:1px solid var(--bdr);border-radius:8px;
      background:linear-gradient(180deg,#0d1b3a,#0a1530);box-shadow:var(--sh);}}
.st{{font-size:14px;font-weight:700;letter-spacing:1px;color:var(--tp);margin-bottom:12px;display:flex;align-items:center;gap:8px;}}
.st::after{{content:"";flex:1;height:1px;background:linear-gradient(90deg,var(--bdr),transparent);}}
.sn{{color:var(--glow);font-size:17px;text-shadow:0 0 6px rgba(56,189,248,.8);}}
.rl2{{display:flex;justify-content:space-between;font-family:"IBM Plex Mono",monospace;font-size:12.5px;margin-bottom:7px;}}
.lo{{color:var(--gn);font-weight:700;}} .mi{{color:var(--ts);}} .hi{{color:var(--rd);font-weight:700;}}
.rt{{position:relative;height:10px;background:linear-gradient(90deg,rgba(22,163,74,.45) 0%,rgba(251,191,36,.35) 50%,rgba(220,38,38,.5) 100%);border-radius:5px;border:1px solid var(--bdr2);}}
.rth{{position:absolute;top:50%;transform:translate(-50%,-50%);width:14px;height:14px;background:#fbbf24;border-radius:50%;box-shadow:0 0 0 2px #050b1a,0 0 10px var(--or);left:{position_pct}%;}}
.rm2{{display:flex;justify-content:space-between;margin-top:7px;font-size:12.5px;color:var(--ts);}}
.kg{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;}}
.kg5{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;}}
.kg6{{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;}}
@media(max-width:700px){{.kg5,.kg6{{grid-template-columns:repeat(2,1fr);}}}}
.kb{{background:var(--inn);border:1px solid var(--bdr);border-radius:6px;padding:12px 14px;}}
.kl{{font-size:12px;letter-spacing:0.5px;color:var(--td);margin-bottom:5px;}}
.kv{{font-family:"IBM Plex Mono",monospace;font-size:22px;font-weight:700;line-height:1;}}
.kv-gn{{color:var(--gn);}} .kv-or{{color:var(--or);}} .kv-rd{{color:var(--rd);}}
.kn{{font-size:11.5px;color:var(--td);margin-top:3px;}}
.tg4{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;}}
@media(max-width:700px){{.tg4{{grid-template-columns:repeat(2,1fr);}}}}
.tb{{background:var(--inn);border:1px solid var(--bdr);border-radius:6px;padding:14px 12px;text-align:center;}}
.tlb{{font-size:13.5px;font-weight:700;color:var(--tp);letter-spacing:1px;margin-bottom:9px;}}
.tbdg{{display:inline-block;font-family:"IBM Plex Mono",monospace;font-size:16px;font-weight:700;padding:5px 14px;border-radius:20px;white-space:nowrap;}}
.bull{{background:var(--rbg);color:var(--rd);border:1px solid var(--rbr);}}
.neu{{background:var(--obg);color:var(--or);border:1px solid var(--obr);}}
.bear{{background:var(--gbg);color:var(--gn);border:1px solid var(--gbr);}}
.tdd{{font-size:13px;color:var(--ts);margin-top:8px;line-height:1.4;}}
.cf{{margin-top:12px;padding:10px 4px;font-size:12px;color:var(--td);line-height:1.6;text-align:center;}}
</style>
</head>
<body>
<div class="wrap">
<div class="card">
<div class="stripe"></div>

<div class="ch">
  <div class="hrow">
    <div>
      <div class="stag">{info.get('market','')}：{stock_id}</div>
      <div class="sname">{info.get('stock_name','')}</div>
      <div class="sectag">{info.get('industry','')}</div>
    </div>
    <div class="prow">
      <div class="pnum">{fmt(close)}</div>
      <div class="pt">{up_arrow} {fmt(change)} ({fmt(change_pct,'%')})</div>
      <div class="pdt">{latest['date']} 收盤</div>
    </div>
  </div>
</div>

<div class="cb">

<div class="sec">
  <div class="st"><span class="sn">①</span> 52 週股價區間</div>
  <div class="rl2">
    <span class="lo">52W 低 ▾ {fmt(low_52w)}</span>
    <span class="mi">目前 {fmt(close)}</span>
    <span class="hi">52W 高 ▴ {fmt(high_52w)}</span>
  </div>
  <div class="rt"><div class="rth"></div></div>
  <div class="rm2">
    <span>位於年區間 <strong>{fmt(position_pct,'%',1)}</strong> 位置</span>
  </div>
</div>

<div class="sec">
  <div class="st"><span class="sn">②</span> 基本面指標（{q_label or "最新季"}）</div>
  <div class="kg5">
    <div class="kb"><div class="kl">EPS（年初累計）</div><div class="kv {'kv-rd' if latest_q.get('eps',0) and latest_q.get('eps',0)>0 else 'kv-gn'}">{fmt(latest_q.get('eps'))}</div><div class="kn">元</div></div>
    <div class="kb"><div class="kl">P/E（本益比・{pe_note}）</div><div class="kv kv-or">{fmt(pe_ratio) if pe_ratio else 'N/A'}</div><div class="kn">倍</div></div>
    <div class="kb"><div class="kl">ROE（年初累計・未年化）</div><div class="kv kv-or">{fmt(latest_q.get('roe'))}</div><div class="kn">%</div></div>
    <div class="kb"><div class="kl">P/B（股價淨值比）</div><div class="kv kv-or">{fmt(pb_now)}</div><div class="kn">倍</div></div>
    <div class="kb"><div class="kl">每股淨值</div><div class="kv">{fmt(latest_q.get('book_value'))}</div><div class="kn">元</div></div>
  </div>
  <div class="kg" style="grid-template-columns:repeat(3,1fr);margin-top:10px;">
    <div class="kb"><div class="kl">營業現金流（年初累計）</div><div class="kv {'kv-rd' if (op_cf or 0)>0 else 'kv-gn'}">{fmt(op_cf,'',1)}</div><div class="kn">億元</div></div>
    <div class="kb"><div class="kl">投資現金流（年初累計）</div><div class="kv">{fmt(inv_cf,'',1)}</div><div class="kn">億元（負值代表投入）</div></div>
    <div class="kb"><div class="kl">自由現金流（營業＋投資）</div><div class="kv {'kv-rd' if (free_cf or 0)>0 else 'kv-gn'}">{fmt(free_cf,'',1)}</div><div class="kn">億元</div></div>
  </div>
</div>

<div class="sec">
  <div class="st"><span class="sn">③</span> 營收表現（最新月）</div>
  <div class="kg">
    <div class="kb"><div class="kl">營收年月</div><div class="kv">{latest_rev.get('year_month','N/A')}</div></div>
    <div class="kb"><div class="kl">月營收</div><div class="kv">{fmt(latest_rev.get('revenue',0)/1000,'',1) if latest_rev.get('revenue') else 'N/A'}</div><div class="kn">百萬元</div></div>
    <div class="kb"><div class="kl">年增率</div><div class="kv {'kv-rd' if (latest_rev.get('yoy_growth') or 0)>0 else 'kv-gn'}">{fmt(latest_rev.get('yoy_growth'),'%')}</div></div>
    <div class="kb"><div class="kl">月增率</div><div class="kv {'kv-rd' if (latest_rev.get('mom_growth') or 0)>0 else 'kv-gn'}">{fmt(latest_rev.get('mom_growth'),'%')}</div></div>
  </div>
</div>

<div class="sec">
  <div class="st"><span class="sn">④</span> 籌碼面（近10日累計＋最新）</div>
  <div class="kg5">
    <div class="kb"><div class="kl">外資買賣超</div><div class="kv {'kv-rd' if foreign_10d>0 else 'kv-gn'}">{foreign_10d:,.0f}</div><div class="kn">股</div></div>
    <div class="kb"><div class="kl">投信買賣超</div><div class="kv {'kv-rd' if sitca_10d>0 else 'kv-gn'}">{sitca_10d:,.0f}</div><div class="kn">股</div></div>
    <div class="kb"><div class="kl">自營商買賣超</div><div class="kv {'kv-rd' if dealers_10d>0 else 'kv-gn'}">{dealers_10d:,.0f}</div><div class="kn">股</div></div>
    <div class="kb"><div class="kl">大戶持股比例</div><div class="kv kv-or">{fmt(latest_holder.get('big_holder_ratio'))}</div><div class="kn">%</div></div>
    <div class="kb"><div class="kl">外資持股比率</div><div class="kv kv-or">{fmt(hold_latest)}</div><div class="kn">%・近20日 <span style="color:{'#dc2626' if (hold_chg_20 or 0)>0 else '#16a34a'};font-weight:700;">{f"{hold_chg_20:+.2f}" if hold_chg_20 is not None else 'N/A'}</span> 百分點</div></div>
  </div>
</div>

<div class="sec">
  <div class="st"><span class="sn">⑤</span> 資金面（融資融券）</div>
  <div class="kg">
    <div class="kb"><div class="kl">融資餘額</div><div class="kv">{f"{latest_margin.get('margin_balance'):,.0f}" if latest_margin.get('margin_balance') else 'N/A'}</div><div class="kn">張</div></div>
    <div class="kb"><div class="kl">融資使用率</div><div class="kv kv-or">{fmt(latest_util.get('margin_rate'),'%') if latest_util.get('margin_rate') is not None else 'N/A'}</div></div>
    <div class="kb"><div class="kl">融券餘額</div><div class="kv">{f"{latest_margin.get('short_balance'):,.0f}" if latest_margin.get('short_balance') else 'N/A'}</div><div class="kn">張</div></div>
    <div class="kb"><div class="kl">融券使用率</div><div class="kv kv-or">{fmt(latest_util.get('short_rate'),'%') if latest_util.get('short_rate') is not None else 'N/A'}</div></div>
  </div>
</div>

<div class="sec">
  <div class="st"><span class="sn">⑥</span> 技術面燈號</div>
  <div class="tg4">
    <div class="tb">
      <div class="tlb">RSI</div>
      <div class="tbdg {'bear' if rsi_status=='超買' else ('bull' if rsi_status=='超賣' else 'neu')}">{fmt(rsi_val)}</div>
      <div class="tdd">{rsi_status}</div>
    </div>
    <div class="tb">
      <div class="tlb">KD</div>
      <div class="tbdg {'bear' if kd_status=='超買' else ('bull' if kd_status=='超賣' else 'neu')}">K:{fmt(kd_k,'',0)} D:{fmt(kd_d,'',0)}</div>
      <div class="tdd">{kd_status}</div>
    </div>
    <div class="tb">
      <div class="tlb">MACD</div>
      <div class="tbdg {'bull' if (macd_hist or 0)>0 else 'bear'}">{fmt(macd_hist)}</div>
      <div class="tdd">{'柱狀轉強' if (macd_hist or 0)>0 else '柱狀轉弱'}</div>
    </div>
    <div class="tb">
      <div class="tlb">股價位階</div>
      <div class="tbdg {'bear' if position_pct>70 else ('bull' if position_pct<30 else 'neu')}">{fmt(position_pct,'%',0)}</div>
      <div class="tdd">{'年區間高位' if position_pct>70 else ('年區間低位' if position_pct<30 else '年區間中段')}</div>
    </div>
    {extra_tech_html}
  </div>
</div>

</div>
<div class="cf">
  資料來源：證交所、櫃買中心、集保結算所、公開資訊觀測站｜資料截至 {latest['date']}｜本卡片僅呈現資料庫真實數據，不含目標價與主觀評等，僅供參考。
</div>
</div>
</div>
</body>
</html>
"""

# st.components.v1.html 已被 Streamlit 標為淘汰，改用 st.iframe；高度跟著內容自動調整，不必另外捲動
st.iframe(html, height="content")

# ===== 葛蘭碧與每日選股分析（和自選股頁相同的判斷）=====
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
@st.cache_data(ttl=3600)
def load_momentum_row(stock_id):
    since = str(pd.Timestamp.today().date() - pd.Timedelta(days=20))
    df = get_rows_for_stocks("momentum_signals", [stock_id], since=since)
    return df.iloc[-1].to_dict() if not df.empty else None

st.markdown("---")
st.subheader("🚀 動能條件（趨勢模板＋52 週新高）")
mrow = load_momentum_row(stock_id)
if mrow is None:
    st.info("還沒有這檔股票的動能資料（每天收盤後計算，新上市不滿一年的股票沒有 52 週資料）。")
else:
    mm = momentum_summary(mrow)
    conds, n_ok, tt_text, tt_note, bo_text, bo_note = (mm["conds"], mm["n_ok"], mm["tt_text"], mm["tt_note"], mm["bo_text"], mm["bo_note"])
    rs_m, c_m, stop_m = mm["rs"], mm["close"], mm["stop"]
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
