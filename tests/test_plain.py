"""白話說明：名詞小辭典、個股分析卡的白話總結"""
import pandas as pd


def test_glossary_terms_exist():
    from utils.glossary import GLOSSARY, PAGES
    for page, info in PAGES.items():
        assert info["intro"]
        missing = [t for t in info["terms"] if t not in GLOSSARY]
        assert not missing, (page, missing)


def _metrics(closes, **kw):
    prices = pd.DataFrame({"close_price": closes})
    m = {"close": closes[-1], "ma20": None, "ma60": None, "prices_ta": prices, "position_pct": 50,
         "latest_rev": {}, "foreign_10d": 0, "sitca_10d": 0, "latest_util": {}, "latest_q": {},
         "q_label": "", "pe_ratio": None, "rsi_val": 50, "vol_ratio": 1.0, "change": 0}
    m.update(kw)
    return m


def test_plain_summary_strong_stock():
    from utils.plain import DOWN, UP, plain_summary
    closes = [100.0] * 20 + [120.0]
    idx = pd.DataFrame({"close": [100.0] * 20 + [102.0]})
    m = _metrics(closes, ma20=105, ma60=100, position_pct=95, latest_rev={"yoy_growth": 35.0, "year_month": "2026-09"},
                 foreign_10d=2_000_000, sitca_10d=500_000, latest_q={"eps": 5.2}, q_label="2026 年前 2 季累計",
                 pe_ratio=18.0, vol_ratio=2.5, change=3.0)
    out = plain_summary(m, idx, "加權指數")
    text = "\n".join(t for _, t in out)
    assert "月線和季線之上" in text and "比大盤強" in text and "成長 35.0%" in text
    assert "外資和投信都在買（外資買 2,000 張、投信買 500 張）" in text
    assert "每股賺 5.20 元" in text and "本益比 18.0 倍" in text and "很多人搶著買" in text
    assert all(icon != DOWN for icon, _ in out) and out[0][0] == UP


def test_plain_summary_weak_stock_and_missing_data():
    from utils.plain import DOWN, plain_summary
    closes = [100.0] * 20 + [80.0]
    m = _metrics(closes, ma20=90, ma60=95, position_pct=5, latest_rev={"yoy_growth": -12.0},
                 foreign_10d=-1_000_000, sitca_10d=-1_000, latest_util={"margin_rate": 25.0},
                 latest_q={"eps": -0.5}, rsi_val=15)
    out = plain_summary(m, pd.DataFrame({"close": [100.0] * 21}))
    text = "\n".join(t for _, t in out)
    assert "月線和季線之下" in text and "比大盤弱" in text and "衰退 12.0%" in text and "都在賣" in text
    assert "融資使用率 25.0%" in text and "目前虧損" in text and "跌得很急" in text
    assert sum(icon == DOWN for icon, _ in out) >= 5
    # 沒有資料時不出錯，也不亂講
    assert plain_summary(_metrics([100.0]), None) == []


def test_card_ai_summary_and_fallback(use_db, monkeypatch):
    from test_pages import APP, errors, run_page
    from utils import plain
    use_db()
    seen = {}

    def fake_ai(stock_id, date, context):
        seen.update(stock_id=stock_id, context=context)
        return "整體偏普通。\n- 股價在季線之上\n要留意：營收成長不多。"

    monkeypatch.setattr(plain, "_cached", fake_ai)
    at = run_page(APP / "pages" / "2_個股分析卡.py", card_stock_id="3583")
    assert errors(at) == []
    texts = " ".join(md.value for md in at.markdown)
    assert "AI 白話總結" in texts and "要留意：營收成長不多" in texts
    assert seen["stock_id"] == "3583" and "系統依規則整理的重點" in seen["context"] and "收盤價" in seen["context"]

    def broken(*_):
        raise RuntimeError("quota")

    monkeypatch.setattr(plain, "_cached", broken)
    at = run_page(APP / "pages" / "2_個股分析卡.py", card_stock_id="3583")
    assert errors(at) == []
    assert "股價在" in " ".join(md.value for md in at.markdown)
    assert any("AI 暫時無法使用（RuntimeError）" in c.value for c in at.caption)
