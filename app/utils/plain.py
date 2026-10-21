"""
個股分析卡的「白話總結」：依卡片上的數字，用固定規則寫成幾句白話，給不太懂股票的人看。
不用 AI，同樣的數字一定寫出同樣的句子；只描述現況，不給買賣建議。
"""
import pandas as pd

UP, DOWN, NOTE = "🔴", "🟢", "⚪"   # 紅色偏多、綠色偏空（台股習慣），其他是中性提醒
LOTS = 1000                          # 1 張 = 1000 股


def _num(v):
    v = pd.to_numeric(v, errors="coerce")
    return None if v is None or pd.isna(v) else float(v)


def _field(m, name, key):
    """卡片的 latest_rev 等可能是 dict 或 pandas Series（也可能是空的 {}）"""
    row = m.get(name)
    return None if row is None else row.get(key)


def _period_return(close, days=20):
    """最近 days 個交易日的漲跌幅（%）"""
    s = pd.to_numeric(pd.Series(close), errors="coerce").dropna()
    if len(s) <= days or s.iloc[-days - 1] <= 0:
        return None
    return (s.iloc[-1] / s.iloc[-days - 1] - 1) * 100


def plain_summary(m, index_df=None, index_name="大盤"):
    """回傳 [(燈號, 句子)]，m 是 utils.card.card_metrics 的結果"""
    out = []
    close, ma20, ma60 = _num(m["close"]), m.get("ma20"), m.get("ma60")

    # 1. 走勢：股價和月線、季線的位置
    if close is not None and ma20 is not None and ma60 is not None:
        if close >= ma20 and close >= ma60:
            out.append((UP, "股價在月線和季線之上，代表最近一個月、一季買進的人平均都是賺錢的，走勢偏強。"))
        elif close < ma20 and close >= ma60:
            out.append((NOTE, "股價跌破月線、但還在季線之上，比較像上漲途中的回檔，可以看接下來能不能站回月線。"))
        elif close >= ma20 and close < ma60:
            out.append((NOTE, "股價站上月線、但還在季線之下，可能開始反彈，不過中期走勢還沒轉強。"))
        else:
            out.append((DOWN, "股價在月線和季線之下，最近買進的人大多是賠錢的，走勢偏弱，不急著進場。"))

    # 2. 和大盤比：近 20 個交易日
    stock_ret = _period_return(m["prices_ta"]["close_price"])
    index_ret = _period_return(index_df["close"]) if index_df is not None and not index_df.empty else None
    if stock_ret is not None and index_ret is not None:
        diff = stock_ret - index_ret
        text = f"近一個月{'漲' if stock_ret >= 0 else '跌'} {abs(stock_ret):.1f}%，{index_name}同期{'漲' if index_ret >= 0 else '跌'} {abs(index_ret):.1f}%"
        if diff >= 3:
            out.append((UP, text + "，比大盤強。"))
        elif diff <= -3:
            out.append((DOWN, text + "，比大盤弱。"))
        else:
            out.append((NOTE, text + "，和大盤差不多。"))

    # 3. 一年區間的位置
    pos = _num(m.get("position_pct"))
    if pos is not None:
        if pos >= 90:
            out.append((UP, "股價接近一年來的最高價。創新高的股票上面沒有急著解套賣出的人，回測顯示這類股票常會繼續強。"))
        elif pos <= 10:
            out.append((DOWN, "股價接近一年來的最低價，代表一路在跌。便宜不代表會漲，要先等止跌。"))

    # 4. 營收
    yoy = _num(_field(m, "latest_rev", "yoy_growth"))
    ym = _field(m, "latest_rev", "year_month") or ""
    if yoy is not None:
        when = f"最新月營收（{ym}）" if ym else "最新月營收"
        if yoy >= 20:
            out.append((UP, f"{when}比去年同月成長 {yoy:.1f}%，生意明顯變好。營收成長是回測中最穩定有效的指標之一。"))
        elif yoy > 0:
            out.append((NOTE, f"{when}比去年同月小幅成長 {yoy:.1f}%。"))
        else:
            out.append((DOWN, f"{when}比去年同月衰退 {abs(yoy):.1f}%，生意變差。"))

    # 5. 法人：近 10 個交易日
    f10 = (_num(m.get("foreign_10d")) or 0) / LOTS
    s10 = (_num(m.get("sitca_10d")) or 0) / LOTS
    if f10 or s10:
        parts = f"外資{'買' if f10 >= 0 else '賣'} {abs(f10):,.0f} 張、投信{'買' if s10 >= 0 else '賣'} {abs(s10):,.0f} 張"
        if f10 > 0 and s10 > 0:
            out.append((UP, f"近 10 天外資和投信都在買（{parts}），專業投資機構偏看好。"))
        elif f10 < 0 and s10 < 0:
            out.append((DOWN, f"近 10 天外資和投信都在賣（{parts}），專業投資機構偏保守。"))
        elif f10 > 0 or s10 > 0:
            who = "外資" if f10 > 0 else "投信"
            out.append((NOTE, f"近 10 天主要是{who}在買（{parts}），看法還不一致。"))

    # 6. 融資
    rate = _num(_field(m, "latest_util", "margin_rate"))
    if rate is not None and rate >= 20:
        out.append((DOWN, f"融資使用率 {rate:.1f}%，借錢買的人偏多。股價一跌容易一起被迫賣出，波動可能比較大。"))

    # 7. 獲利
    eps = _num(_field(m, "latest_q", "eps"))
    if eps is not None:
        label = m.get("q_label") or "最新財報"
        if eps > 0:
            pe = _num(m.get("pe_ratio"))
            pe_text = ""
            if pe is not None:
                pe_text = f"；以現在股價算，大約 {pe:.0f} 年能賺回股價（本益比 {pe:.1f} 倍）"
                if pe >= 40:
                    pe_text += "，股價已經反映很高的期待"
            out.append((NOTE, f"公司有賺錢，{label}每股賺 {eps:.2f} 元{pe_text}。"))
        else:
            out.append((DOWN, f"公司目前虧損，{label}每股虧 {abs(eps):.2f} 元。"))

    # 8. 短線過熱或超跌
    rsi = _num(m.get("rsi_val"))
    if rsi is not None and rsi >= 80:
        out.append((NOTE, f"最近漲得很急（RSI {rsi:.0f}），短線可能震盪；不過強勢股常常會續強，不必只因為這樣就賣。"))
    elif rsi is not None and rsi <= 20:
        out.append((NOTE, f"最近跌得很急（RSI {rsi:.0f}），可能有短線反彈，但跌勢中搶反彈風險高。"))

    # 9. 成交量
    vr, chg = _num(m.get("vol_ratio")), _num(m.get("change"))
    if vr is not None and vr >= 2 and chg is not None:
        if chg > 0:
            out.append((UP, f"今天成交量是平常的 {vr:.1f} 倍、而且上漲，很多人搶著買。"))
        elif chg < 0:
            out.append((DOWN, f"今天成交量是平常的 {vr:.1f} 倍、而且下跌，賣壓很重，要小心。"))
    return out


def render_plain_summary(m, index_df=None, index_name="大盤"):
    import streamlit as st
    lines = plain_summary(m, index_df, index_name)
    if not lines:
        return
    with st.container(border=True):
        st.markdown("**📝 白話總結**")
        st.markdown("\n".join(f"- {icon} {text}" for icon, text in lines))
        st.caption("依下方數字自動整理，🔴 偏好、🟢 偏壞、⚪ 中性；只描述現況，不是買賣建議。")
