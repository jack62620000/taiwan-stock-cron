import pandas as pd

# 動能選股（momentum.py 每天收盤後計算，寫進 momentum_signals）：條件與回測說明，每日選股頁與個股分析卡共用
RS_MIN = 70
BREAKOUT_VOL = 1.5
HOT_PCT = 30   # 收盤在 50 日均線之上這麼多 % 以上算「漲多」，清單直接剔除（回測 60 日已沒有優勢）

BACKTEST_MD = f"""
回測期間 2025/1～2026/10，20 日均量 500 張以上的普通股，訊號出現**隔天開盤買進**，還沒扣約 0.6% 的交易成本：

| 方法 | 次數 | 20 日平均 | 60 日平均 | 60 日勝率 |
|---|---|---|---|---|
| 全部股票（對照） | 346,000 | +1.8% | +7.0% | 49% |
| 趨勢模板 | 53,120 | +4.3% | +14.7% | 52% |
| 52 週新高＋量增 | 2,711 | +3.9% | +12.5% | 51% |
| 兩者同時 | 1,361 | +5.2% | +17.2% | 55% |

- 平均報酬大約是全部股票的兩倍，2025、2026 兩年分開看都贏。
- 勝率只有五成上下，賺的是少數大漲的股票，所以一定要設停損、讓賺錢的股票續抱。
- 這段期間台股偏多頭，動能型方法在空頭時可能失靈。

**和全部股票平均比的 60 日超額報酬**（同一段期間、隔天開盤買）：

| 方法 | 次數 | 60 日比平均 |
|---|---|---|
| 趨勢模板（全部） | 52,282 | +5.6% |
| 趨勢模板，離 50 日線 < 30% | 39,256 | +7.5% |
| 趨勢模板，離 50 日線 ≥ 30%（漲多） | 13,026 | +0.2% |
| 趨勢中回檔再起 | 350 | +11.0% |

- 已經漲太多的股票之後沒有優勢，所以清單剔除離 50 日線 {HOT_PCT}% 以上的股票（個股分析卡仍會顯示）。
- 趨勢中回檔再起是這次測過最好的，但次數少，平均一天不到一檔，前後兩段都比平均好。
- 只用「碰過布林下軌再站上中軌」、不限趨勢模板的話，回測比平均差，一定要在上升趨勢裡才有用。
"""

RULES_MD = f"""
**趨勢模板（Mark Minervini）**：8 個條件全部符合才算，代表股票處在明確的上升趨勢。
1. 收盤在 150 日、200 日均線之上
2. 150 日均線在 200 日均線之上
3. 200 日均線比 20 個交易日前高（至少上揚一個月）
4. 50 日均線在 150 日、200 日均線之上
5. 收盤在 50 日均線之上
6. 收盤比 52 週低點高 30% 以上
7. 收盤在 52 週高點的 25% 以內
8. 相對強度（RS）至少 {RS_MIN}：近 3、6、9、12 個月漲幅加權（最近 3 個月占 40%），贏過 {RS_MIN}% 的股票

**52 週新高突破＋量增**：收盤創 52 週新高，而且成交量至少是前 50 日均量的 {BREAKOUT_VOL:g} 倍；
前 10 個交易日內已經突破過的不重複算。

**趨勢中回檔再起**：符合趨勢模板，前 10 個交易日內收盤碰過布林下軌（20 日均線 − 2 個標準差），
今天收盤由下往上站上布林中軌（20 日均線），也就是上升趨勢裡回檔後重新轉強。

**剔除漲多**：收盤在 50 日均線之上 {HOT_PCT}% 以上的股票不列入清單。

只判斷 20 日均量 500 張以上的股票。均線、高低點都用還原權值後的股價計算。
"""


def _ok(a, b, op):
    if a is None or b is None or pd.isna(a) or pd.isna(b):
        return None
    return op(float(a), float(b))


def conditions(r):
    """一檔股票的 8 個趨勢模板條件：[(符合與否（資料不足為 None）, 條件, 目前數值)]"""
    gt = lambda a, b: a > b
    ge = lambda a, b: a >= b
    c, ma50, ma150, ma200 = r.get("close_price"), r.get("ma50"), r.get("ma150"), r.get("ma200")
    hi, lo, rs = r.get("high_52w"), r.get("low_52w"), r.get("rs_rating")

    def f(v):
        return "—" if v is None or pd.isna(v) else f"{float(v):,.2f}"

    def both(x, y):
        return None if x is None or y is None else (x and y)

    return [
        (both(_ok(c, ma150, gt), _ok(c, ma200, gt)), "收盤在 150 日、200 日均線之上",
         f"收盤 {f(c)}、150 日 {f(ma150)}、200 日 {f(ma200)}"),
        (_ok(ma150, ma200, gt), "150 日均線在 200 日均線之上", f"{f(ma150)} vs {f(ma200)}"),
        (_ok(ma200, r.get("ma200_prev"), gt), "200 日均線上揚（比 20 個交易日前高）",
         f"現在 {f(ma200)}、20 天前 {f(r.get('ma200_prev'))}"),
        (both(_ok(ma50, ma150, gt), _ok(ma50, ma200, gt)), "50 日均線在 150 日、200 日均線之上", f"50 日 {f(ma50)}"),
        (_ok(c, ma50, gt), "收盤在 50 日均線之上", f"收盤 {f(c)}、50 日 {f(ma50)}"),
        (_ok(c, None if lo is None or pd.isna(lo) else float(lo) * 1.3, ge), "比 52 週低點高 30% 以上",
         f"52 週低點 {f(lo)}" + (f"（高 {(float(c) / float(lo) - 1) * 100:.0f}%）" if _ok(c, lo, gt) is not None else "")),
        (_ok(c, None if hi is None or pd.isna(hi) else float(hi) * 0.75, ge), "在 52 週高點的 25% 以內",
         f"52 週高點 {f(hi)}" + (f"（差 {(float(c) / float(hi) - 1) * 100:+.1f}%）" if _ok(c, hi, gt) is not None else "")),
        (_ok(rs, RS_MIN, ge), f"相對強度 RS ≥ {RS_MIN}",
         "RS " + ("—（成交量太小，不列入排名）" if rs is None or pd.isna(rs) else f"{int(rs)}")),
    ]


def momentum_summary(mrow):
    """
    一檔股票動能資料（momentum_signals 一列）的摘要：趨勢模板、52 週新高突破、RS、停損價。
    個股分析卡與 LINE 推播共用。
    """
    from utils.granville import stop_price
    conds = conditions(mrow)
    n_ok = sum(1 for ok, _, _ in conds if ok)
    liquid_m = bool(mrow.get("liquid"))
    if not liquid_m:
        tt_text, tt_note = f"{n_ok}/8", "20 日均量不到 500 張，不列入判斷"
    elif mrow.get("trend_template"):
        tt_text, tt_note = "✅ 符合", "今天剛符合" if mrow.get("trend_new") else "8 個條件全部符合"
        if mrow.get("pullback"):
            tt_note = "今天趨勢中回檔再起（碰過布林下軌後站上中軌）"
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
    return {"conds": conds, "n_ok": n_ok, "tt_text": tt_text, "tt_note": tt_note,
            "bo_text": bo_text, "bo_note": bo_note, "rs": rs_m, "close": c_m, "stop": stop_m}


def off_ma50(df):
    """收盤離 50 日均線 %"""
    return (pd.to_numeric(df["close_price"], errors="coerce") / pd.to_numeric(df["ma50"], errors="coerce") - 1) * 100


def momentum_choices(liq):
    """
    動能選股頁的分類（每日選股頁與 LINE 共用）：{名稱: 布林 Series}；liq 是 20 日均量夠的股票。
    離 50 日均線 HOT_PCT% 以上（漲多）的股票不列入
    """
    flag = lambda c: liq[c].fillna(False).astype(bool) if c in liq else pd.Series(False, index=liq.index)  # noqa: E731
    keep = ~(off_ma50(liq) >= HOT_PCT)
    tt = flag("trend_template")
    return {
        "兩者同時": tt & flag("breakout") & keep,
        "趨勢中回檔再起": flag("pullback") & keep,
        "52 週新高＋量增": flag("breakout") & keep,
        "今天剛符合趨勢模板": flag("trend_new") & keep,
        "符合趨勢模板（全部）": tt & keep,
    }


def hot_count(liq):
    """有動能訊號（趨勢模板或新高突破）但因為漲多被剔除的檔數"""
    sig = liq["trend_template"].fillna(False).astype(bool) | liq["breakout"].fillna(False).astype(bool)
    return int((sig & (off_ma50(liq) >= HOT_PCT)).sum())


def momentum_table(view):
    """
    動能選股表的欄位（每日選股頁與 LINE 共用）：離 52 週高點 %、離 50 日線 %、停損價、每張風險、趨勢模板／新高突破打勾，
    依 RS、量比由高到低排序。view 需要 close_price、ma50、high_52w、atr14、rs_rating、vol_ratio、trend_template、breakout
    """
    from utils.granville import stop_price
    view = view.copy()
    close = pd.to_numeric(view["close_price"], errors="coerce")
    view["off_high"] = (close / pd.to_numeric(view["high_52w"], errors="coerce") - 1) * 100
    view["off_ma50"] = off_ma50(view)
    view["stop"] = [stop_price(float(c), a) for c, a in zip(close, pd.to_numeric(view["atr14"], errors="coerce"))]
    view["risk"] = ((close - view["stop"]) * 1000).round()
    view["tt"] = view["trend_template"].map({True: "✅", False: ""})
    view["bo"] = view["breakout"].map({True: "✅", False: ""})
    return view.sort_values(["rs_rating", "vol_ratio"], ascending=False)
