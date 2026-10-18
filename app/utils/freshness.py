from datetime import date, timedelta

def _last_trading_day(now):
    """
    「應該已經有資料」的最近一個交易日：昨天以前的最後一個平日（國定假日無法判斷）。
    每日排程晚上 9 點後才開始抓當天資料、半夜還有補抓，所以當天的資料不列入檢查。
    """
    d = now.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _weekdays_between(start, end):
    """start（不含）到 end（含）之間有幾個平日"""
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def _expected_month(today):
    """月營收每月 10 日前公布上個月，所以 11 日起應該要有上個月的資料"""
    first = today.replace(day=1)
    back = 1 if today.day > 10 else 2
    for _ in range(back):
        first = (first - timedelta(days=1)).replace(day=1)
    return first.strftime("%Y-%m")


def _expected_quarter(today):
    """季報法定期限：Q1 5/15、Q2 8/14、Q3 11/14、年報 3/31；期限隔天起應該要有該季資料"""
    y = today.year
    md = (today.month, today.day)
    if md >= (11, 15):
        return y, 3
    if md >= (8, 15):
        return y, 2
    if md >= (5, 16):
        return y, 1
    if md >= (4, 1):
        return y - 1, 4
    return y - 1, 3


def evaluate(rows, now):
    """
    rows 是 get_data_status() 的結果，now 是台灣時間的 datetime。
    每一列加上 ok（是否正常）與 note（給人看的狀態說明）。
    """
    today = now.date()
    expected_day = _last_trading_day(now)
    out = []
    for r in rows:
        latest, freq = r["latest"], r["freq"]
        ok, note = True, "最新"
        if latest is None:
            ok, note = False, "沒有資料"
        elif freq == "日":
            lag = _weekdays_between(date.fromisoformat(latest), expected_day)
            if lag > 0:
                ok, note = False, f"落後 {lag} 個交易日"
        elif freq == "週":
            days = (today - date.fromisoformat(latest)).days
            if days > 9:
                ok, note = False, f"已 {days} 天沒更新"
        elif freq == "月":
            expected = _expected_month(today)
            if latest < expected:
                ok, note = False, f"應該要有 {expected} 的資料"
        elif freq == "季":
            ey, eq = _expected_quarter(today)
            y, q = latest.split("Q")
            if (int(y), int(q)) < (ey, eq):
                ok, note = False, f"應該要有 {ey}Q{eq} 的資料"
        out.append({**r, "ok": ok, "note": note})
    return out
