"""
首頁「🌎 昨晚美股」：前一晚美股主要指數、台積電 ADR 溢價、美元／台幣，以及費半和隔天加權指數的連動

美股資料在 ETF 專用資料庫的 us_market_daily 表（fetch_us_market.py 每天早上 6:20 寫入），
網站的 secrets 要另外設 ETF_SUPABASE_URL / ETF_SUPABASE_KEY 才讀得到；沒設時只顯示一行提示。
latest_rows、adr_premium、sox_vs_taiex 只用資料計算，不碰 Streamlit，方便測試。
"""
import altair as alt
import pandas as pd
import streamlit as st

from utils.db import get_daily_prices, get_market_index, get_us_market
from utils.ui import SERIES, render_metric_cards, style_sign

# 上方卡片：(代號, 名稱)
CARDS = [("^DJI", "道瓊"), ("^GSPC", "S&P 500"), ("^IXIC", "那斯達克"), ("^SOX", "費城半導體"),
         ("^VIX", "VIX 恐慌指數"), ("TWD=X", "美元／台幣")]
CATEGORIES = {"index": "指數", "mag7": "科技七巨頭", "semi": "AI／半導體", "adr": "台灣 ADR",
              "commodity": "原油", "fx": "匯率", "bond": "美債殖利率"}
ADR_SHARES = 5     # 1 股台積電 ADR＝5 股台積電
LINK_DAYS = 20     # 費半和隔天加權的連動看最近幾次


def latest_rows(us):
    """每個代號最新一筆（依代號排序）"""
    if us is None or us.empty:
        return pd.DataFrame()
    return us.sort_values("date").groupby("symbol", as_index=False).last()


def adr_premium(us, tw_prices):
    """
    台積電 ADR 溢價：ADR 收盤 × 美元／台幣 ÷ 5，和台積電在 ADR 那天（含）以前最近一天的收盤比。
    tw_prices 要有 date、close_price；資料不足回傳 None。
    """
    if us is None or us.empty or tw_prices is None or tw_prices.empty:
        return None
    adr = us[us["symbol"] == "TSM"].sort_values("date")
    fx = us[us["symbol"] == "TWD=X"].sort_values("date")
    if adr.empty or fx.empty:
        return None
    a = adr.iloc[-1]
    rate_rows = fx[fx["date"] <= a["date"]]
    rate = float((rate_rows if not rate_rows.empty else fx).iloc[-1]["close"])
    tw = tw_prices.assign(close_price=pd.to_numeric(tw_prices["close_price"], errors="coerce")).dropna(subset=["close_price"])
    tw = tw[tw["date"] <= a["date"]].sort_values("date")
    if tw.empty:
        return None
    implied = float(a["close"]) * rate / ADR_SHARES
    tw_close = float(tw.iloc[-1]["close_price"])
    return {"adr_date": a["date"], "adr_close": float(a["close"]), "rate": rate, "implied": implied,
            "tw_date": tw.iloc[-1]["date"], "tw_close": tw_close, "premium_pct": (implied / tw_close - 1) * 100}


def sox_vs_taiex(us, taiex, n=LINK_DAYS):
    """
    費半每天的漲跌幅，對照台股「下一個交易日」加權指數的漲跌幅（美股收盤是台灣隔天清晨，台股隔天開盤才反應）。
    回傳最近 n 筆：美股日期、費半%、台股日期、加權%。
    """
    cols = ["美股日期", "費半%", "台股日期", "加權%"]
    if us is None or us.empty or taiex is None or taiex.empty:
        return pd.DataFrame(columns=cols)
    sox = us[(us["symbol"] == "^SOX")].dropna(subset=["change_pct"]).sort_values("date")
    tw = taiex.sort_values("date").assign(close=lambda d: pd.to_numeric(d["close"], errors="coerce"))
    tw = tw.assign(pct=tw["close"].pct_change() * 100).dropna(subset=["pct"])
    rows = []
    for _, s in sox.iterrows():
        nxt = tw[tw["date"] > s["date"]]
        if nxt.empty:
            continue
        rows.append([s["date"], float(s["change_pct"]), nxt.iloc[0]["date"], float(nxt.iloc[0]["pct"])])
    # 美股連假時好幾天會對到同一個台股交易日，只留最靠近的那天
    df = pd.DataFrame(rows, columns=cols).drop_duplicates("台股日期", keep="last")
    return df.tail(n).reset_index(drop=True)


@st.cache_data(ttl=3600)
def load_us_market():
    """回傳 (美股資料或 None, 台積電最近股價, 加權指數, 錯誤訊息)；None 代表網站沒設 ETF 資料庫連線"""
    try:
        us = get_us_market()
    except Exception as ex:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), f"讀取美股資料失敗（{type(ex).__name__}）"
    if us is None or us.empty:
        return us, pd.DataFrame(), pd.DataFrame(), None
    for c in ["close", "change", "change_pct"]:
        us[c] = pd.to_numeric(us[c], errors="coerce")
    return us, get_daily_prices("2330", limit=10, adjust=False), get_market_index("TAIEX", 60), None


def _card(row, symbol, label):
    close, chg, pct = float(row["close"]), row["change"], row["change_pct"]
    digits = 3 if symbol == "TWD=X" else 2
    if pd.isna(chg):
        return (label, f"{close:,.{digits}f}", "", 0)
    pct_text = f"（{pct:+.2f}%）" if pd.notna(pct) else ""
    return (label, f"{close:,.{digits}f}", f"{'▲' if chg > 0 else '▼' if chg < 0 else ''} {abs(chg):,.{digits}f}{pct_text}", chg)


def render():
    us, tw_prices, taiex, error = load_us_market()
    if error:
        st.caption(f"🌎 昨晚美股：{error}，請稍後按「強制重新整理」再試。")
        return
    if us is None:
        st.caption("🌎 昨晚美股：網站還沒設定 ETF 資料庫的連線（Streamlit secrets 的 ETF_SUPABASE_URL、ETF_SUPABASE_KEY），設定後這裡會顯示前一晚美股。")
        return
    if us.empty:
        st.caption("🌎 昨晚美股：還沒有美股資料（每個交易日早上 6:20 更新）。")
        return
    latest = latest_rows(us).set_index("symbol")
    us_date = latest.loc["^GSPC", "date"] if "^GSPC" in latest.index else latest["date"].max()

    st.subheader(f"🌎 昨晚美股（美國 {us_date} 收盤）")
    cards = [_card(latest.loc[sym], sym, label) for sym, label in CARDS if sym in latest.index]
    prem = adr_premium(us, tw_prices)
    if prem:
        cards.append(("台積電 ADR 溢價", f"{prem['premium_pct']:+.2f}%",
                      f"換算 {prem['implied']:,.1f} 元／台股 {prem['tw_close']:,.0f} 元", prem["premium_pct"]))
    render_metric_cards(cards)
    st.caption("紅色是上漲、綠色是下跌。美元／台幣上漲代表台幣貶值，常伴隨外資賣超。"
               f"ADR 溢價＝台積電 ADR × 美元／台幣 ÷ {ADR_SHARES}，和台積電最近一天收盤比，正的代表美股盤比台股貴，常被拿來預估開盤方向。")

    with st.expander("📋 全部美股、原油、匯率、美債"):
        table = latest.reset_index()
        table = pd.DataFrame({
            "分類": table["category"].map(CATEGORIES), "名稱": table["name"], "代號": table["symbol"],
            "收盤": table["close"], "漲跌": table["change"], "漲跌幅%": table["change_pct"], "日期": table["date"],
        })
        table["_order"] = table["分類"].map({v: i for i, v in enumerate(CATEGORIES.values())})
        table = table.sort_values(["_order", "代號"]).drop(columns="_order")
        st.dataframe(
            table.style.format("{:,.2f}", subset=["收盤"], na_rep="")
                 .format("{:+,.2f}", subset=["漲跌"], na_rep="")
                 .format("{:+.2f}", subset=["漲跌幅%"], na_rep="")
                 .map(style_sign, subset=["漲跌", "漲跌幅%"]),
            hide_index=True, width="stretch", height=min(600, 38 + 35 * len(table)),
            column_config={"名稱": st.column_config.TextColumn(pinned=True)},
        )
        st.caption("美債殖利率的單位是 %，漲跌是百分點（0.01＝1 個基點），看漲跌就好。原油是近月期貨，換月那天的漲跌會失真。")

    link = sox_vs_taiex(us, taiex)
    if not link.empty:
        same = int(((link["費半%"] > 0) == (link["加權%"] > 0)).sum())
        with st.expander(f"📈 費半和隔天台股的連動（最近 {len(link)} 次有 {same} 次同方向）"):
            chart = link.melt(id_vars=["台股日期"], value_vars=["費半%", "加權%"], var_name="線", value_name="漲跌幅%")
            chart["線"] = chart["線"].map({"費半%": "費半（前一晚）", "加權%": "加權（隔天）"})
            st.altair_chart(alt.Chart(chart).mark_line(point=alt.OverlayMarkDef(size=25)).encode(
                x=alt.X("台股日期:T", title=None, axis=alt.Axis(format="%m/%d")),
                y=alt.Y("漲跌幅%:Q", title="漲跌幅 %"),
                color=alt.Color("線:N", scale=alt.Scale(range=SERIES[:2]), legend=alt.Legend(orient="top", title=None)),
                tooltip=[alt.Tooltip("台股日期:T", title="台股日期"), "線:N", alt.Tooltip("漲跌幅%:Q", format="+.2f")],
            ), width="stretch")
            st.caption("橫軸是台股日期；費半那條線是它前一晚（美國時間）的漲跌。同方向次數越多，代表最近台股跟美股半導體連動越緊。")
