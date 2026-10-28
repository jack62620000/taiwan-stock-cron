"""
個股分析卡的互動 K 線圖（TradingView Lightweight Charts v4.2.3）

build_charts 只組出圖表設定（list of dict），不碰 Streamlit，方便測試；render_html 把設定變成一段 HTML，
頁面用 st.components.v1.html 顯示。圖表程式庫放在 app/assets（Apache 2.0 授權，見同目錄的 LICENSE），
直接嵌進頁面，不必連外部 CDN。
上下幾張圖共用時間軸、十字線同步：主圖 K 線＋均線／布林通道＋葛蘭碧買賣點標記＋價位線，下面是成交量和一張副圖；
滑鼠移到哪一天，左上角就顯示那天的開高低收、漲跌、成交量和各條線的數值。
"""
import json
from pathlib import Path

import pandas as pd

from utils.granville import RULES, STABLE_RULES, rule_frames
from utils.ui import ACCENT, BG_COLOR, BORDER, DOWN_COLOR, GLOW, MUTED, TEXT, UP_COLOR

NO_TRADE_COLOR = "#9ca3af"
MA_COLORS = {"MA5": ACCENT, "MA20": GLOW, "MA60": "#a78bfa"}
BB_COLOR = "#64748b"
SUB_CHARTS = ["外資買賣超", "MACD", "KD"]


def _chart(height, time_visible=False):
    return {
        "height": height,
        "layout": {"background": {"type": "solid", "color": BG_COLOR}, "textColor": TEXT, "fontSize": 12},
        "grid": {"vertLines": {"color": BORDER + "55"}, "horzLines": {"color": BORDER + "55"}},
        "rightPriceScale": {"borderColor": BORDER, "minimumWidth": 72},
        "timeScale": {"borderColor": BORDER, "visible": time_visible},
        "crosshair": {"mode": 0},
        # 指定語系：瀏覽器語系設定特殊時（例如 en-US@posix）圖會畫不出來
        "localization": {"locale": "zh-TW", "dateFormat": "yyyy/MM/dd"},
    }


def _line(df, col, color, title, width=2, style=0):
    data = [{"time": t, "value": round(float(v), 2)} for t, v in zip(df["time"], df[col]) if pd.notna(v)]
    return {"type": "Line", "data": data,
            "options": {"color": color, "lineWidth": width, "lineStyle": style, "title": title,
                        "priceLineVisible": False, "crosshairMarkerVisible": False}}


def _hist(df, col, colors, title, fmt=None):
    data = [{"time": t, "value": float(v), "color": c}
            for t, v, c in zip(df["time"], df[col], colors) if pd.notna(v)]
    opts = {"title": title, "priceLineVisible": False}
    if fmt:
        opts["priceFormat"] = fmt
    return {"type": "Histogram", "data": data, "options": opts}


def granville_markers(df, full_close, rules=tuple(STABLE_RULES)):
    """
    圖上的葛蘭碧標記（預設只標回測穩定的法則，和「均線買賣點」預設相同）：
    買進法則在 K 棒下方紅色 ▲、賣出法則在上方綠色 ▼、過熱（法則 8）上方灰點。
    full_close：完整期間的收盤價（以日期為索引），均線要用比圖更長的資料才算得出來。
    """
    f = rule_frames(full_close.to_frame("s"))
    shown = set(df["date"])
    out = []
    for d in full_close.index:
        if d not in shown:
            continue
        hit = [n for n in rules if bool(f["hits"][n].at[d, "s"])]
        if not hit:
            continue
        sides = {RULES[n][0] for n in hit}
        t = d   # 日期字串 YYYY-MM-DD 就是圖的時間
        label = "、".join(str(n) for n in hit)
        if "買進" in sides:
            out.append({"time": t, "position": "belowBar", "color": UP_COLOR, "shape": "arrowUp",
                        "text": f"買{label}" if sides == {"買進"} else label})
        elif "賣出" in sides:
            out.append({"time": t, "position": "aboveBar", "color": DOWN_COLOR, "shape": "arrowDown", "text": f"賣{label}"})
        else:
            out.append({"time": t, "position": "aboveBar", "color": MUTED, "shape": "circle", "text": "過熱"})
    return out


def build_charts(prices_ta, days, overlays=("均線",), sub="外資買賣超", ins=None, levels=None, markers=True):
    """
    prices_ta：個股分析卡算好指標的日線（date、open/high/low/close_price、volume、no_trade、MA5/20/60、BB_UP/LOW、K、D、MACD_HIST）；
    days：顯示最近幾個交易日；overlays：主圖要疊的「均線」「布林通道」；sub：副圖（SUB_CHARTS 其中之一或 None）；
    ins：法人買賣超（date、foreign_net_shares、sitca_net_shares）；levels：主圖水平價位線 {名稱: (價格, 顏色)}。
    回傳每張圖的設定 [{"chart": 圖表選項, "series": [{"type", "data", "options", "markers"?, "priceLines"?}]}]。
    """
    full = prices_ta.copy()
    full["date"] = full["date"].astype(str)
    for c in ["open_price", "high_price", "low_price", "close_price", "volume"]:
        full[c] = pd.to_numeric(full[c], errors="coerce")
    df = full.tail(days).reset_index(drop=True)
    df["time"] = df["date"]
    no_trade = df["no_trade"].fillna(False).astype(bool) if "no_trade" in df.columns else pd.Series(False, index=df.index)
    up = df["close_price"] >= df["open_price"]
    bar_color = [NO_TRADE_COLOR if n else UP_COLOR if u else DOWN_COLOR for n, u in zip(no_trade, up)]

    candles = []
    for r, color in zip(df.itertuples(), bar_color):
        if pd.isna(r.close_price):
            continue
        o = r.open_price if pd.notna(r.open_price) else r.close_price
        h = r.high_price if pd.notna(r.high_price) else max(o, r.close_price)
        lo = r.low_price if pd.notna(r.low_price) else min(o, r.close_price)
        candles.append({"time": r.time, "open": round(float(o), 2), "high": round(float(h), 2),
                        "low": round(float(lo), 2), "close": round(float(r.close_price), 2),
                        "color": color, "borderColor": color, "wickColor": color})
    price = {"type": "Candlestick", "data": candles,
             "options": {"upColor": UP_COLOR, "downColor": DOWN_COLOR, "borderVisible": False,
                         "wickUpColor": UP_COLOR, "wickDownColor": DOWN_COLOR}}
    if markers:
        full_close = full.set_index("date")["close_price"].where(~full.set_index("date")["no_trade"].fillna(False).astype(bool)) \
            if "no_trade" in full.columns else full.set_index("date")["close_price"]
        price["markers"] = granville_markers(df, full_close)
    # 水平價位線（停損價、觸發價、52 週高低）：右側價格軸標出價格、價格右邊寫名稱，不會撐大縱軸範圍
    price["priceLines"] = [
        {"price": round(float(p), 2), "color": c, "lineWidth": 1, "lineStyle": 2, "axisLabelVisible": True, "title": name}
        for name, (p, c) in (levels or {}).items() if p is not None and pd.notna(p)
    ]
    main = [price]
    if "均線" in overlays:
        main += [_line(df, c, MA_COLORS[c], c) for c in MA_COLORS if c in df.columns and df[c].notna().any()]
    if "布林通道" in overlays and "BB_UP" in df.columns and df["BB_UP"].notna().any():
        main += [_line(df, "BB_UP", BB_COLOR, "布林上軌", 1, 2)]
        if "BB_MID" in df.columns and df["BB_MID"].notna().any():   # 中軌就是 20 日均線
            main += [_line(df, "BB_MID", BB_COLOR, "布林中軌", 1, 1)]
        main += [_line(df, "BB_LOW", BB_COLOR, "布林下軌", 1, 2)]

    vol = df.assign(lots=df["volume"] / 1000)
    charts = [
        {"chart": _chart(360), "series": main},
        {"chart": _chart(110, time_visible=sub is None),
         "series": [_hist(vol, "lots", bar_color, "成交量（張）", {"type": "volume"})]},
    ]
    sub_series = None
    if sub == "外資買賣超" and ins is not None and not ins.empty and "foreign_net_shares" in ins.columns:
        f = ins[["date", "foreign_net_shares"]].copy()
        f["date"] = f["date"].astype(str)
        f = df[["date", "time"]].merge(f, on="date", how="left")
        f["lots"] = pd.to_numeric(f["foreign_net_shares"], errors="coerce") / 1000
        sub_series = [_hist(f, "lots", [UP_COLOR if v >= 0 else DOWN_COLOR for v in f["lots"].fillna(0)],
                            "外資買賣超（張）", {"type": "volume"})]
    elif sub == "MACD" and "MACD_HIST" in df.columns:
        h = pd.to_numeric(df["MACD_HIST"], errors="coerce")
        sub_series = [_hist(df.assign(h=h), "h", [UP_COLOR if v >= 0 else DOWN_COLOR for v in h.fillna(0)], "MACD 柱")]
    elif sub == "KD" and "K" in df.columns:
        sub_series = [_line(df.assign(k=pd.to_numeric(df["K"], errors="coerce")), "k", ACCENT, "K"),
                      _line(df.assign(d=pd.to_numeric(df["D"], errors="coerce")), "d", GLOW, "D")]
    if sub_series:
        charts.append({"chart": _chart(130, time_visible=True), "series": sub_series})
    else:
        charts[1]["chart"]["timeScale"]["visible"] = True
    return charts


LIB = Path(__file__).resolve().parent.parent / "assets" / "lightweight-charts.standalone.production.js"

_TEMPLATE = """
<style>
  body { margin: 0; background: __BG__; font-family: 'Noto Sans TC', sans-serif; }
  #legend { color: __TEXT__; font-size: 13px; line-height: 20px; min-height: 40px; padding: 2px 4px 4px; }
  #legend b { font-weight: 600; }
  #legend .k { color: __MUTED__; margin-left: 10px; }
  .row { display: flex; }
  .names { position: relative; width: __NAMES_W__px; flex: none; overflow: hidden; }
  .names div { position: absolute; left: 4px; font-size: 12px; line-height: 16px; white-space: nowrap; }
  .names .v { display: inline-block; min-width: 48px; padding: 0 4px; margin-right: 6px; border-radius: 2px; text-align: right; }
  .names .n { margin-left: 62px; }   /* 沒有數值框的（成交量）名稱和其他名稱對齊 */
</style>
<div id="legend"></div>
<div id="charts"></div>
<script>__LIB__</script>
<script>
(function () {
  const CONF = __CONF__;
  const UP = "__UP__", DOWN = "__DOWN__";
  const box = document.getElementById("charts");
  const NAMES_W = __NAMES_W__;      // 價格軸右邊放指標數值和名稱的欄寬
  const charts = [], lookups = [];   // lookups：每條線 {名稱, 顏色, 日期→值}
  const names = [];                  // names[i]：第 i 張圖要標的線 {名稱, 顏色, 價格（函式）, 線, 小數位, 數值是否也放右欄}
  let candleMap = null, prevClose = {}, volMap = null;
  const fmt = (v, d = 2) => v == null ? "—" : v.toLocaleString("zh-TW", {minimumFractionDigits: d, maximumFractionDigits: d});
  CONF.forEach((c, i) => {
    const row = document.createElement("div"), el = document.createElement("div"), col = document.createElement("div");
    row.className = "row"; col.className = "names"; col.style.height = c.chart.height + "px";
    row.append(el, col);
    box.appendChild(row);
    const chart = LightweightCharts.createChart(el, Object.assign({}, c.chart, {width: (box.clientWidth || 700) - NAMES_W}));
    charts.push(chart);
    names.push({col: col, items: []});
    c.series.forEach(s => {
      const opt = s.options || {};
      // 價格軸只留股價和成交量；指標（均線、價位線、副圖）的數值和名稱都放到價格軸右邊的欄位
      const isVol = i === 1 && s.type === "Histogram";
      const digits = (opt.priceFormat || {}).type === "volume" ? 0 : 2;
      const api = chart["add" + s.type + "Series"](Object.assign({}, opt, {title: ""}, isVol ? {} : {lastValueVisible: false}));
      api.setData(s.data);
      if (s.markers && s.markers.length) api.setMarkers(s.markers);
      (s.priceLines || []).forEach(pl => {
        api.createPriceLine(Object.assign({}, pl, {title: "", axisLabelVisible: false}));
        if (pl.title) names[i].items.push({text: pl.title, color: pl.color, price: () => pl.price, api: api, digits: 2, value: true});
      });
      const last = s.data[s.data.length - 1];
      if (opt.title && last && opt.lastValueVisible !== false)
        names[i].items.push({text: opt.title, color: opt.color || last.color, price: () => last.value, api: api,
                             digits: digits, value: !isVol});
      const m = new Map(s.data.map(d => [d.time, d]));
      if (s.type === "Candlestick") {
        candleMap = m;
        s.data.forEach((d, k) => { if (k > 0) prevClose[d.time] = s.data[k - 1].close; });
      } else if (i === 1 && !volMap) {
        volMap = m;
      } else {
        lookups.push({name: (s.options || {}).title || "", color: (s.options || {}).color, map: m, chart: i});
      }
      if (!charts[i]._first) charts[i]._first = api;
    });
  });

  function legend(time) {
    const el = document.getElementById("legend");
    const c = candleMap && candleMap.get(time);
    if (!c) { el.innerHTML = ""; return; }
    const pc = prevClose[time];
    const chg = pc ? (c.close / pc - 1) * 100 : null;
    const col = chg == null ? "inherit" : chg > 0 ? UP : chg < 0 ? DOWN : "inherit";
    const v = volMap && volMap.get(time);
    let html = "<b>" + time.replaceAll("-", "/") + "</b>"
      + "<span class='k'>開</span> " + fmt(c.open) + "<span class='k'>高</span> " + fmt(c.high)
      + "<span class='k'>低</span> " + fmt(c.low) + "<span class='k'>收</span> <b style='color:" + col + "'>" + fmt(c.close) + "</b>"
      + (chg == null ? "" : " <span style='color:" + col + "'>(" + (chg > 0 ? "+" : "") + chg.toFixed(2) + "%)</span>")
      + (v ? "<span class='k'>量</span> " + fmt(v.value, 0) + " 張" : "");
    let extra = "";
    lookups.forEach(l => {
      const p = l.map.get(time);
      if (p) extra += "<span class='k' style='color:" + (l.color || "inherit") + "'>" + l.name + "</span> " + fmt(p.value, l.name.includes("張") ? 0 : 2);
    });
    el.innerHTML = html + "<br>" + extra;
  }

  // 拖拉、縮放同步：任一張圖動了，其他圖跟著同一段日期
  let syncing = false;
  charts.forEach(ch => ch.timeScale().subscribeVisibleLogicalRangeChange(r => {
    if (syncing || !r) return;
    syncing = true;
    charts.forEach(o => { if (o !== ch) o.timeScale().setVisibleLogicalRange(r); });
    syncing = false;
  }));
  // 十字線同步，並更新左上角的數值；滑鼠離開圖時顯示最後一天
  const lastTime = candleMap ? Array.from(candleMap.keys()).pop() : null;
  charts.forEach(ch => ch.subscribeCrosshairMove(p => {
    const t = p && p.time;
    legend(t || lastTime);
    charts.forEach(o => {
      if (o === ch) return;
      if (t && o._first) o.setCrosshairPosition(NaN, t, o._first); else o.clearCrosshairPosition();
    });
  }));
  legend(lastTime);

  // 價格軸右邊的欄位：指標數值（線的顏色底）＋名稱，對準那條線的高度；縮放或拖拉後每一格畫面重新對齊
  const ink = c => {   // 底色亮就用深色字
    const m = /^#(..)(..)(..)/.exec(c || "");
    if (!m) return "#ffffff";
    const [r, g, b] = m.slice(1).map(x => parseInt(x, 16));
    return 0.299 * r + 0.587 * g + 0.114 * b > 150 ? "__BG__" : "#ffffff";
  };
  names.forEach(n => n.items.forEach(it => {
    it.el = document.createElement("div");
    const col = it.color || "__TEXT__";
    if (it.value) {
      const v = document.createElement("span");
      v.className = "v"; v.style.background = col; v.style.color = ink(col);
      v.textContent = fmt(it.price(), it.digits);
      it.el.appendChild(v);
    }
    const t = document.createElement("span");
    t.textContent = it.text; t.style.color = col;
    if (!it.value) t.className = "n";
    it.el.appendChild(t);
    n.col.appendChild(it.el);
  }));
  function placeNames() {
    names.forEach(n => {
      const h = n.col.clientHeight;
      const pos = n.items.map(it => ({it: it, y: it.api.priceToCoordinate(it.price())}))
        .filter(p => { const ok = p.y != null && p.y >= 0 && p.y <= h; p.it.el.style.display = ok ? "" : "none"; return ok; })
        .sort((a, b) => a.y - b.y);
      // 靠太近的名稱併成一組，整組上下置中在這幾條線的平均高度，避免全部往下擠而離線太遠
      const groups = [];
      pos.forEach(p => {
        groups.push({ys: [p.y], items: [p.it]});
        while (groups.length > 1) {
          const g = groups[groups.length - 1], f = groups[groups.length - 2];
          const top = gr => gr.ys.reduce((a, b) => a + b, 0) / gr.ys.length - gr.ys.length * 8;
          if (top(f) + f.ys.length * 16 <= top(g)) break;
          f.ys.push(...g.ys); f.items.push(...g.items); groups.pop();
        }
      });
      groups.forEach(g => {
        const top = g.ys.reduce((a, b) => a + b, 0) / g.ys.length - g.ys.length * 8;
        g.items.forEach((it, k) => { it.el.style.top = (top + k * 16) + "px"; });
      });
    });
    requestAnimationFrame(placeNames);
  }
  requestAnimationFrame(placeNames);
  charts.forEach(ch => ch.timeScale().fitContent());
  new ResizeObserver(() => charts.forEach(ch => ch.applyOptions({width: box.clientWidth - NAMES_W}))).observe(box);
})();
</script>
"""


def render_html(charts):
    """把 build_charts 的設定變成可以直接顯示的 HTML（含圖表程式庫），回傳 (html, 高度)"""
    html = (_TEMPLATE.replace("__LIB__", LIB.read_text(encoding="utf-8"))
            .replace("__CONF__", json.dumps(charts, ensure_ascii=False))
            .replace("__BG__", BG_COLOR).replace("__TEXT__", TEXT).replace("__MUTED__", MUTED)
            .replace("__UP__", UP_COLOR).replace("__DOWN__", DOWN_COLOR).replace("__NAMES_W__", "170"))
    return html, sum(c["chart"]["height"] for c in charts) + 52
