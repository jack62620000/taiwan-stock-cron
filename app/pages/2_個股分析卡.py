import streamlit as st
import pandas as pd
from datetime import datetime
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.db import (
    get_stock_info, get_daily_prices, get_quarterly,
    get_revenue, get_stock_ins, get_margin, get_holder,
    get_margin_utilization  # ✅ 新增
)
from utils.indicators import calc_all, get_latest_signals

st.set_page_config(page_title="個股分析卡", page_icon="🗂️", layout="wide")
st.title("🗂️ 個股深度分析卡")

# ===== 股票搜尋 =====
stock_id = st.text_input("輸入股票代號", value="2330", placeholder="例如: 2330")
if not stock_id:
    st.info("請輸入股票代號")
    st.stop()

# ===== 載入資料 =====
@st.cache_data(ttl=300)
def load_card_data(stock_id):
    info        = get_stock_info(stock_id)
    prices      = get_daily_prices(stock_id, limit=250)
    quarterly   = get_quarterly(stock_id, limit=8)
    revenue     = get_revenue(stock_id, limit=12)
    ins         = get_stock_ins(stock_id, limit=10)
    margin      = get_margin(stock_id, limit=1)
    holder      = get_holder(stock_id, limit=1)
    utilization = get_margin_utilization(stock_id, limit=60)  # ✅ 新增
    return info, prices, quarterly, revenue, ins, margin, holder, utilization

info, prices, quarterly, revenue, ins, margin, holder, utilization = load_card_data(stock_id)

if not info:
    st.error(f"找不到股票代號：{stock_id}")
    st.stop()

if prices.empty:
    st.error("無股價資料")
    st.stop()

# ===== 計算數據 =====
prices_ta = calc_all(prices.copy())
signals   = get_latest_signals(prices_ta)

latest    = prices.iloc[-1]
prev      = prices.iloc[-2] if len(prices) > 1 else latest
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

foreign_10d  = ins["foreign_net_shares"].sum() if not ins.empty else 0
sitca_10d    = ins["sitca_net_shares"].sum() if not ins.empty else 0
dealers_10d  = ins["dealers_net_shares"].sum() if not ins.empty else 0

# ===== 計算本益比 =====
pe_ratio = None
pe_note  = ""
if not quarterly.empty:
    q_sorted  = quarterly.sort_values(["year", "quarter"])
    recent_4q = q_sorted.tail(4)
    eps_list  = recent_4q["eps"].dropna().tolist()
    if len(eps_list) == 4:
        annual_eps = sum(eps_list)
        pe_note    = "近4季"
    elif len(eps_list) >= 1:
        annual_eps = eps_list[-1] * 4
        pe_note    = "單季×4"
    else:
        annual_eps = None
    if annual_eps and annual_eps > 0 and close:
        pe_ratio = round(close / annual_eps, 2)

up_color = "#dc2626" if change >= 0 else "#16a34a"
up_arrow = "▲" if change >= 0 else "▼"

rsi_val    = signals.get("RSI", {}).get("value")
rsi_status = signals.get("RSI", {}).get("status", "N/A")
kd_k       = signals.get("KD", {}).get("K")
kd_d       = signals.get("KD", {}).get("D")
kd_status  = signals.get("KD", {}).get("status", "N/A")
macd_hist  = signals.get("MACD", {}).get("hist")

def fmt(v, suffix="", decimals=2):
    if v is None or pd.isna(v):
        return "N/A"
    return f"{v:.{decimals}f}{suffix}"

def fmt_rate(v):
    """融資融券使用率格式化，加上顏色"""
    if v is None or pd.isna(v):
        return "N/A"
    if v >= 20:
        return f'<span style="color:#dc2626;font-weight:700;">{v:.2f}%</span>'
    elif v >= 10:
        return f'<span style="color:#ea580c;font-weight:600;">{v:.2f}%</span>'
    else:
        return f"{v:.2f}%"

# ===== 建立 HTML 卡片 =====
html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700;900&family=JetBrains+Mono:wght@400;700&display=swap');
:root{{
  --bg:#fef6f0;--card:#fff;--inn:#fffaf5;
  --bdr:#fce4d0;--bdr2:#fbc99e;
  --th:#451a03;--tp:#6b2e0c;--ts:#8a5430;--td:#c89668;
  --gn:#16a34a;--gbg:#dcfce7;--gbr:#bbf7d0;--gdk:#14532d;
  --or:#ea580c;--obg:#ffedd5;--obr:#fed7aa;--odk:#7c2d12;
  --rd:#dc2626;--rbg:#fee2e2;--rbr:#fecaca;--rdk:#7f1d1d;
  --am:#b45309;--ambg:#fef3c7;--ambr:#fde68a;
  --sh:0 6px 30px rgba(69,26,3,.1),0 1px 5px rgba(69,26,3,.05);
}}
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:var(--bg);font-family:"Noto Sans TC",sans-serif;color:var(--tp);}}
.wrap{{max-width:920px;margin:0 auto;padding:16px;}}
.card{{background:var(--card);border-radius:20px;box-shadow:var(--sh);border:1px solid var(--bdr);overflow:hidden;}}
.stripe{{height:4px;background:linear-gradient(90deg,#ea580c,#dc2626,#b45309,#7c2d12);}}
.ch{{padding:24px 28px 18px;border-bottom:2px solid var(--bdr);}}
.hrow{{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap;}}
.stag{{font-family:"JetBrains Mono",monospace;font-size:11px;font-weight:700;letter-spacing:2px;color:var(--or);background:var(--obg);border:1px solid var(--obr);padding:3px 9px;border-radius:4px;display:inline-block;margin-bottom:6px;}}
.sname{{font-size:32px;font-weight:900;color:var(--th);line-height:1;}}
.sectag{{display:inline-flex;align-items:center;gap:5px;font-size:12px;color:var(--ts);background:var(--inn);border:1px solid var(--bdr);border-radius:20px;padding:4px 12px;margin-top:8px;}}
.prow{{display:flex;align-items:baseline;gap:12px;margin-top:16px;flex-wrap:wrap;}}
.pnum{{font-family:"JetBrains Mono",monospace;font-size:44px;font-weight:700;color:var(--th);line-height:1;}}
.pt{{font-family:"JetBrains Mono",monospace;font-size:14px;font-weight:700;padding:3px 9px;border-radius:6px;color:{up_color};background:{('#fee2e2' if change >= 0 else '#dcfce7')};}}
.pdt{{font-size:11px;color:var(--td);align-self:flex-end;padding-bottom:5px;}}
.cb{{padding:0 28px;}}
.sec{{padding:20px 0;border-bottom:1px solid var(--bdr);}}
.sec:last-child{{border-bottom:none;}}
.st{{font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:var(--td);margin-bottom:13px;display:flex;align-items:center;gap:8px;}}
.st::after{{content:"";flex:1;height:1px;background:var(--bdr);}}
.sn{{color:var(--or);}}
.rl2{{display:flex;justify-content:space-between;font-family:"JetBrains Mono",monospace;font-size:11px;margin-bottom:7px;}}
.lo{{color:var(--rd);font-weight:700;}} .mi{{color:var(--ts);}} .hi{{color:var(--gn);font-weight:700;}}
.rt{{position:relative;height:10px;background:linear-gradient(90deg,#fee2e2 0%,#fef3c7 50%,#dcfce7 100%);border-radius:5px;border:1px solid var(--bdr2);}}
.rth{{position:absolute;top:50%;transform:translate(-50%,-50%);width:14px;height:14px;background:#fff;border-radius:50%;box-shadow:0 0 0 2px #fff,0 0 0 4px var(--or);left:{position_pct}%;}}
.rm2{{display:flex;justify-content:space-between;margin-top:7px;font-size:11px;color:var(--ts);}}
.kg{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;}}
.kg5{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;}}
.kg6{{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;}}
@media(max-width:700px){{.kg5,.kg6{{grid-template-columns:repeat(2,1fr);}}}}
.kb{{background:var(--inn);border:1px solid var(--bdr);border-radius:10px;padding:12px 14px;}}
.kl{{font-size:9.5px;letter-spacing:0.5px;color:var(--td);margin-bottom:5px;}}
.kv{{font-family:"JetBrains Mono",monospace;font-size:22px;font-weight:700;line-height:1;}}
.kv-gn{{color:var(--gn);}} .kv-or{{color:var(--or);}} .kv-rd{{color:var(--rd);}}
.kn{{font-size:10px;color:var(--td);margin-top:3px;}}
.tg4{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;}}
.tb{{background:var(--inn);border:1px solid var(--bdr);border-radius:8px;padding:10px 12px;text-align:center;}}
.tlb{{font-size:9.5px;color:var(--td);letter-spacing:1px;text-transform:uppercase;margin-bottom:6px;}}
.tbdg{{display:inline-block;font-size:11px;font-weight:700;padding:3px 8px;border-radius:20px;white-space:nowrap;}}
.bull{{background:var(--gbg);color:var(--gn);border:1px solid var(--gbr);}}
.neu{{background:var(--obg);color:var(--or);border:1px solid var(--obr);}}
.bear{{background:var(--rbg);color:var(--rd);border:1px solid var(--rbr);}}
.tdd{{font-size:10px;color:var(--td);margin-top:5px;line-height:1.4;}}
.cf{{background:#fffaf5;border-top:1px solid var(--bdr);padding:12px 28px;font-size:10px;color:var(--td);line-height:1.6;}}
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
  </div>
  <div class="prow">
    <div class="pnum">{fmt(close)}</div>
    <div class="pt">{up_arrow} {fmt(change)} ({fmt(change_pct,'%')})</div>
    <div class="pdt">{latest['date']} 收盤</div>
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
  <div class="st"><span class="sn">②</span> 基本面指標（最新季）</div>
  <div class="kg5">
    <div class="kb"><div class="kl">EPS（每股盈餘）</div><div class="kv {'kv-gn' if latest_q.get('eps',0) and latest_q.get('eps',0)>0 else 'kv-rd'}">{fmt(latest_q.get('eps'))}</div><div class="kn">元</div></div>
    <div class="kb"><div class="kl">P/E（本益比・{pe_note}）</div><div class="kv kv-or">{fmt(pe_ratio) if pe_ratio else 'N/A'}</div><div class="kn">倍</div></div>
    <div class="kb"><div class="kl">ROE（股東權益報酬率）</div><div class="kv kv-or">{fmt(latest_q.get('roe'))}</div><div class="kn">%</div></div>
    <div class="kb"><div class="kl">P/B（股價淨值比）</div><div class="kv kv-or">{fmt(latest_q.get('pb_ratio'))}</div><div class="kn">倍</div></div>
    <div class="kb"><div class="kl">每股淨值</div><div class="kv">{fmt(latest_q.get('book_value'))}</div><div class="kn">元</div></div>
  </div>
</div>

<div class="sec">
  <div class="st"><span class="sn">③</span> 營收表現（最新月）</div>
  <div class="kg">
    <div class="kb"><div class="kl">營收年月</div><div class="kv">{latest_rev.get('year_month','N/A')}</div></div>
    <div class="kb"><div class="kl">月營收</div><div class="kv">{fmt(latest_rev.get('revenue',0)/1000,'',1) if latest_rev.get('revenue') else 'N/A'}</div><div class="kn">百萬元</div></div>
    <div class="kb"><div class="kl">年增率</div><div class="kv {'kv-gn' if (latest_rev.get('yoy_growth') or 0)>0 else 'kv-rd'}">{fmt(latest_rev.get('yoy_growth'),'%')}</div></div>
    <div class="kb"><div class="kl">月增率</div><div class="kv {'kv-gn' if (latest_rev.get('mom_growth') or 0)>0 else 'kv-rd'}">{fmt(latest_rev.get('mom_growth'),'%')}</div></div>
  </div>
</div>

<div class="sec">
  <div class="st"><span class="sn">④</span> 籌碼面（近10日累計＋最新）</div>
  <div class="kg">
    <div class="kb"><div class="kl">外資買賣超</div><div class="kv {'kv-gn' if foreign_10d>0 else 'kv-rd'}">{foreign_10d:,.0f}</div><div class="kn">股</div></div>
    <div class="kb"><div class="kl">投信買賣超</div><div class="kv {'kv-gn' if sitca_10d>0 else 'kv-rd'}">{sitca_10d:,.0f}</div><div class="kn">股</div></div>
    <div class="kb"><div class="kl">自營商買賣超</div><div class="kv {'kv-gn' if dealers_10d>0 else 'kv-rd'}">{dealers_10d:,.0f}</div><div class="kn">股</div></div>
    <div class="kb"><div class="kl">大戶持股比例</div><div class="kv kv-or">{fmt(latest_holder.get('big_holder_ratio'))}</div><div class="kn">%</div></div>
  </div>
</div>

<div class="sec">
  <div class="st"><span class="sn">⑤</span> 資金面（融資融券）</div>
  <div class="kg">
    <div class="kb"><div class="kl">融資餘額</div><div class="kv">{f"{latest_margin.get('margin_balance'):,.0f}" if latest_margin.get('margin_balance') else 'N/A'}</div><div class="kn">股</div></div>
    <div class="kb"><div class="kl">融資使用率</div><div class="kv kv-or">{fmt(latest_util.get('margin_rate'),'%') if latest_util.get('margin_rate') is not None else 'N/A'}</div></div>
    <div class="kb"><div class="kl">融券餘額</div><div class="kv">{f"{latest_margin.get('short_balance'):,.0f}" if latest_margin.get('short_balance') else 'N/A'}</div><div class="kn">股</div></div>
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

st.components.v1.html(html, height=1500, scrolling=True)