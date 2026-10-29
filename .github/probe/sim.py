import numpy as np

COST = 0.003  # 單邊交易成本（手續費＋證交稅攤一半），來回約 0.6%
MAX_HOLD = 250


def simulate(sig, rank, O, C, ex, slots, start_i, hold=None, init_stop=None, group=None, cap=None):
    """
    sig[t, n]：第 t 天收盤出現買進訊號，第 t+1 天開盤買進；rank 越大越優先。
    ex[t, n]：第 t 天收盤出現賣出訊號（第 t+1 天開盤賣出）；hold 給定時改成固定持有 hold 天（第 entry+hold 天開盤賣）。
    init_stop：(atr 寬表, 倍數, 最大%) 時用 ATR 停損＋移動停利，ex 不用。
    回傳 (trades, equity)。trades：(n, 進場 t, 出場 t, 報酬, 是否已出場)
    """
    T, N = C.shape
    Cf = C.copy()
    for t in range(1, T):
        m = np.isnan(Cf[t])
        Cf[t, m] = Cf[t - 1, m]
    cash, pos, trades = 1.0, {}, []
    equity = np.full(T, np.nan)
    eq_prev = 1.0
    for t in range(start_i, T):
        # 1. 賣出（前一天收盤決定、或固定持有到期），今天開盤賣
        for n in list(pos):
            p = pos[n]
            due = p["pending"] or (hold is not None and t - p["t"] >= hold) or t - p["t"] >= MAX_HOLD
            if due and not np.isnan(O[t, n]):
                cash += p["units"] * O[t, n] * (1 - COST)
                trades.append((n, p["t"], t, O[t, n] * (1 - COST) / (p["px"] * (1 + COST)) - 1, True))
                del pos[n]
        # 2. 買進（前一天收盤的訊號），今天開盤買
        free = slots - len(pos)
        if free > 0 and t >= 1:
            cand = np.where(sig[t - 1] & ~np.isnan(O[t]))[0]
            cand = [n for n in cand if n not in pos]
            if cand:
                cand.sort(key=lambda n: -rank[t - 1, n])
                if cap is not None:
                    cnt = {}
                    for m in pos:
                        cnt[group[m]] = cnt.get(group[m], 0) + 1
                    keep = []
                    for n in cand:
                        if len(keep) >= free:
                            break
                        g = group[n]
                        if g is not None and cnt.get(g, 0) >= cap:
                            continue
                        cnt[g] = cnt.get(g, 0) + 1
                        keep.append(n)
                    cand = keep
                for n in cand[:free]:
                    alloc = min(cash, eq_prev / slots) if slots < 1000 else 1.0
                    if alloc <= 1e-9:
                        break
                    if slots < 1000:
                        cash -= alloc
                    px = O[t, n]
                    stop = -np.inf
                    if init_stop is not None:
                        atr, mult, mx = init_stop
                        a = atr[t - 1, n]
                        stop = px * (1 - mx) if np.isnan(a) else max(px - mult * a, px * (1 - mx))
                    pos[n] = {"t": t, "px": px, "units": alloc / (px * (1 + COST)), "pending": False,
                              "peak": px, "stop": stop}
        # 3. 收盤：算資產、判斷明天要不要賣
        val = sum(p["units"] * Cf[t, n] for n, p in pos.items() if not np.isnan(Cf[t, n]))
        eq_prev = cash + val
        equity[t] = eq_prev
        for n, p in pos.items():
            c = C[t, n]
            if np.isnan(c):
                continue
            if init_stop is not None:
                atr, mult, mx = init_stop
                p["peak"] = max(p["peak"], c)
                a = atr[t, n]
                if not np.isnan(a):
                    p["stop"] = max(p["stop"], p["peak"] - mult * a)
                p["pending"] = c < p["stop"]
            elif hold is None:
                p["pending"] = bool(ex[t, n])
    for n, p in pos.items():
        trades.append((n, p["t"], T - 1, Cf[T - 1, n] * (1 - COST) / (p["px"] * (1 + COST)) - 1, False))
    return trades, equity


def summarize(trades, equity, dates, start_i, mid_i):
    import pandas as pd
    if not trades:
        return {"交易數": 0}
    r = np.array([x[3] for x in trades]) * 100
    days = np.array([x[2] - x[1] for x in trades])
    out = {"交易數": len(trades), "平均報酬%": round(r.mean(), 2), "中位數%": round(np.median(r), 2),
           "勝率%": round((r > 0).mean() * 100, 1), "平均持有天": round(days.mean(), 1),
           "賺賠比": round(r[r > 0].mean() / -r[r <= 0].mean(), 2) if (r <= 0).any() and (r > 0).any() else np.nan}
    eq = pd.Series(equity[start_i:])
    if eq.notna().any() and eq.iloc[-1] != 1.0:
        yrs = (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[start_i])).days / 365.25
        dd = (eq / eq.cummax() - 1).min() * 100
        out.update({"總報酬%": round((eq.iloc[-1] - 1) * 100, 1), "年化%": round((eq.iloc[-1] ** (1 / yrs) - 1) * 100, 1),
                    "最大回撤%": round(dd, 1),
                    "前半%": round((equity[mid_i] / equity[start_i] - 1) * 100, 1),
                    "後半%": round((equity[-1] / equity[mid_i] - 1) * 100, 1)})
    return out
