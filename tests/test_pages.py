"""每一頁用假資料跑一次（有資料、只有股票清單兩種情況），確認頁面沒有錯誤"""
import pytest
from streamlit.testing.v1 import AppTest

from conftest import APP

PAGES = sorted(p.name for p in (APP / "pages").glob("*.py"))


def run_page(path, **state):
    at = AppTest.from_file(str(path), default_timeout=120)
    at.secrets["USERNAME"] = "tester"
    at.secrets["PASSWORD"] = "secret"
    at.session_state["authenticated"] = True
    at.session_state["user"] = "tester"
    at.session_state["user_name"] = "測試"
    at.session_state["primary"] = True
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    return at


def errors(at):
    return [e.value for e in at.exception]


@pytest.mark.parametrize("page", PAGES)
def test_page_with_data(use_db, page):
    use_db()
    at = run_page(APP / "pages" / page)
    assert errors(at) == []


@pytest.mark.parametrize("page", PAGES)
def test_page_with_empty_db(use_db, page):
    use_db(empty=True)
    at = run_page(APP / "pages" / page)
    assert errors(at) == []


@pytest.mark.parametrize("tab", ["🛒 明日買進", "🏆 評分排行", "📐 均線買賣點", "🤖 明日買進模擬"])
@pytest.mark.parametrize("empty", [False, True])
def test_picks_tabs(use_db, tab, empty):
    """選股頁的每個分頁各自都要能顯示"""
    use_db(empty=empty)
    at = run_page(APP / "pages" / "1_選股.py", picks_tab=tab)
    assert errors(at) == []


def test_home_page(use_db):
    use_db()
    at = run_page(APP / "main.py")
    assert errors(at) == []
    assert any("昨晚美股" in h.value for h in at.subheader)
    assert any("台積電 ADR 溢價" in m.value for m in at.markdown)


def test_home_page_without_etf_db(use_db):
    import utils.db as db
    use_db()
    db.etf_supabase = None
    at = run_page(APP / "main.py")
    assert errors(at) == []
    assert not any("昨晚美股" in h.value for h in at.subheader)
    assert any("ETF_SUPABASE_URL" in c.value for c in at.caption)


def test_login_form_shown_when_logged_out(use_db):
    use_db()
    at = AppTest.from_file(str(APP / "main.py"), default_timeout=120)
    at.secrets["USERNAME"] = "tester"
    at.secrets["PASSWORD"] = "secret"
    at.run()
    assert errors(at) == []
    assert any(t.label == "密碼" for t in at.text_input)


@pytest.mark.parametrize("stock_id", ["2330", "6488", "2317"])
def test_stock_card_each_market(use_db, stock_id):
    """個股分析卡：上市、上櫃、空頭股都要能顯示"""
    use_db()
    at = run_page(APP / "pages" / "2_個股分析卡.py", card_stock_id=stock_id)
    assert errors(at) == []


def test_stock_card_shows_all_sections(use_db):
    use_db()
    at = run_page(APP / "pages" / "2_個股分析卡.py", card_stock_id="3583")
    assert errors(at) == []
    heads = " ".join(h.value for h in at.subheader)
    for title in ["均線買賣點", "評分排行", "同產業比較", "動能條件", "股價走勢", "相對大盤強弱", "三大法人買賣超", "外資持股比率"]:
        assert title in heads
    assert "這頁在看什麼" in " ".join(i.value for i in at.info)
    labels = [e.label for e in at.expander]
    assert any("這頁怎麼看" in x for x in labels) and any("名詞小辭典" in x for x in labels)
    html = " ".join(str(getattr(e, "proto", "")) for e in at.main)
    assert "比 5 日前" in html or "和 5 日前差不多" in html  # 技術指標下面有和 5 日前比的變化
    assert "近 3 月年增率" in html and "券資比" in html  # 營收、資金面的延伸數據


@pytest.mark.parametrize("period", ["週線", "月線"])
def test_stock_card_weekly_monthly(use_db, period):
    """K 線切換週線、月線：圖照畫、說明寫出共幾根 K 棒"""
    use_db()
    at = run_page(APP / "pages" / "2_個股分析卡.py", card_stock_id="2330")
    next(r for r in at.radio if r.key == "kline_period").set_value(period).run()
    assert errors(at) == []
    assert any(c.value.startswith(f"{period}：每根 K 棒") for c in at.caption)


def test_stock_card_recent(use_db):
    """最近查看：列出這次看過的其他股票，點一下就換成那檔"""
    use_db()
    at = run_page(APP / "pages" / "2_個股分析卡.py", card_stock_id="2330", card_recent=["2317", "2330", "3583"])
    assert errors(at) == []
    pills = at.get("button_group")[0]
    assert pills.label == "最近查看" and pills.proto.options[0].content == "2317 鴻海"
    assert [o.content for o in pills.proto.options] == ["2317 鴻海", "3583 辛耘"]
    pills.set_value("3583 辛耘").run()
    assert errors(at) == []
    assert at.selectbox[0].value.startswith("3583 ")
    assert at.session_state["card_recent"][:2] == ["3583", "2330"]


def test_add_to_watchlist_from_stock_card(use_db):
    client = use_db()
    at = run_page(APP / "pages" / "2_個股分析卡.py", card_stock_id="1101")
    next(b for b in at.button if b.label.startswith("⭐ 加入自選股")).click().run()
    assert errors(at) == []
    assert {"group_id": 1, "stock_id": "1101"}.items() <= next(
        r for r in client.tables["watchlist"] if r["stock_id"] == "1101").items()


def test_watchlist_batch_add(use_db):
    """自選股頁一次貼上多檔：代號、名稱都可以，已經在清單和找不到的分開列出，按一下全部加入"""
    client = use_db()
    at = run_page(APP / "pages" / "8_自選股.py")
    box = next(x for x in at.text_area if x.key == "watch_batch")
    box.set_value("2454, 台泥、2330\n9999 2454").run()
    assert errors(at) == []
    note = " ".join(c.value for c in at.caption)
    assert "會加入 2 檔：2454 聯發科、1101 台泥" in note and "已經在清單：2330" in note and "找不到：9999" in note
    next(b for b in at.button if b.label.startswith("➕ 全部加入")).click().run()
    assert errors(at) == []
    ids = [r["stock_id"] for r in client.tables["watchlist"] if r["group_id"] == 1]
    assert {"2454", "1101", "2330"} <= set(ids) and len(ids) == len(set(ids))
    assert at.session_state["watch_batch"] == ""


def test_add_to_watchlist_keeps_existing_note(use_db):
    """已經在自選股的股票再加一次，原本的備註不會被蓋掉"""
    import utils.db as db
    client = use_db()
    existing = client.tables["watchlist"][0]
    existing["note"] = "等回測月線再買"
    db.add_to_watchlist(existing["group_id"], existing["stock_id"], "")
    rows = [r for r in client.tables["watchlist"]
            if r["group_id"] == existing["group_id"] and r["stock_id"] == existing["stock_id"]]
    assert [r["note"] for r in rows] == ["等回測月線再買"]


PICKS_PAGE = "1_選股.py"  # 葛蘭碧、每日選股都是選股頁的分頁


@pytest.mark.parametrize("empty", [False, True])
def test_granville_followup_tab(use_db, empty):
    use_db(empty=empty)
    at = run_page(APP / "pages" / PICKS_PAGE, picks_tab="📐 均線買賣點", granville_followup=True)
    assert errors(at) == []


def test_granville_followup_table(use_db, monkeypatch):
    """有買點時顯示卡片與表格（假資料剛好沒有法則 2 買點，借動能訊號的結果來顯示）"""
    use_db()
    import utils.analysis as analysis
    date, df = analysis.momentum_followup()
    monkeypatch.setattr(analysis, "granville_followup", lambda: (date, df.drop(columns="signal")))
    at = run_page(APP / "pages" / PICKS_PAGE, picks_tab="📐 均線買賣點", granville_followup=True)
    assert errors(at) == []
    assert any("至今%" in list(d.value.columns) or "ret_now" in list(d.value.columns) for d in at.dataframe)


def test_momentum_followup_section(use_db):
    use_db()
    at = run_page(APP / "pages" / PICKS_PAGE, picks_tab="🏆 評分排行")
    assert errors(at) == []
    assert any("ret_now" in list(d.value.columns) for d in at.dataframe)


def test_picks_auto_account_tab(use_db):
    """選股頁「明日買進模擬」分頁：顯示自動帳戶成績（總資產走勢）與持有中、已賣出紀錄"""
    use_db()
    at = run_page(APP / "pages" / "1_選股.py", picks_tab="🤖 明日買進模擬")
    assert errors(at) == []
    heads = [h.value for h in at.subheader]
    assert any("自動帳戶成績" in h for h in heads)
    assert "📈 持有中" in heads and "✅ 已賣出" in heads


@pytest.mark.parametrize("page", ["8_自選股.py", "10_模擬持股.py"])
def test_auto_group_hidden_from_group_picker(use_db, page):
    """自動帳戶的分組不出現在自選股、模擬持股頁的分組選單；之前選過也會換回一般分組"""
    use_db()
    at = run_page(APP / "pages" / page, group_id=11)
    assert errors(at) == []
    picker = next(s for s in at.selectbox if s.label == "📁 分組")
    assert 11 not in picker.options and "每日買進測試" not in picker.options
    assert at.session_state["group_id"] == 1


@pytest.mark.parametrize("tab", ["🔎 ETF 總覽", "🗂️ ETF 分析卡", "📅 配息行事曆", "🧮 存股試算"])
@pytest.mark.parametrize("empty", [False, True])
def test_etf_tabs(use_db, tab, empty):
    """ETF 頁的每個分頁各自都要能顯示（有資料、ETF 資料庫還沒有資料兩種情況）"""
    use_db(empty=empty)
    at = run_page(APP / "pages" / "6_ETF.py", etf_tab=tab)
    assert errors(at) == []


@pytest.mark.parametrize("etf_id", ["0050", "00878", "00679B", "00632R"])
def test_etf_card_each(use_db, etf_id):
    """ETF 分析卡：有分割、有追蹤指數、債券、沒有月資料（槓桿反向）都要能顯示"""
    use_db()
    at = run_page(APP / "pages" / "6_ETF.py", etf_tab="🗂️ ETF 分析卡", etf_card_id=etf_id)
    assert errors(at) == []
    assert at.selectbox[0].value.startswith(f"{etf_id} ")
    heads = " ".join(h.value for h in at.subheader)
    assert "股價走勢" in heads and "籌碼" in heads


@pytest.mark.parametrize("period", ["週線", "月線"])
def test_etf_card_weekly_monthly(use_db, period):
    use_db()
    at = run_page(APP / "pages" / "6_ETF.py", etf_tab="🗂️ ETF 分析卡", etf_card_id="0050")
    next(r for r in at.radio if r.key == "etf_kline_period").set_value(period).run()
    assert errors(at) == []


def test_etf_page_without_etf_db(use_db):
    import utils.db as db
    use_db()
    db.etf_supabase = None
    at = run_page(APP / "pages" / "6_ETF.py")
    assert errors(at) == []
    assert any("ETF_SUPABASE_URL" in i.value for i in at.info)


def test_etf_savings_without_reinvest(use_db):
    use_db()
    at = run_page(APP / "pages" / "6_ETF.py", etf_tab="🧮 存股試算")
    next(t for t in at.toggle if t.key == "etf_sv_reinvest").set_value(False).run()
    assert errors(at) == []
    assert any("試算結果" in h.value for h in at.subheader)


@pytest.mark.parametrize("period", ["週線", "月線"])
def test_etf_card_range_applies_to_weekly_monthly(use_db, period):
    """週線、月線也照「顯示區間」只畫那段期間的 K 棒"""
    use_db()
    at = run_page(APP / "pages" / "6_ETF.py", etf_tab="🗂️ ETF 分析卡", etf_card_id="0050")
    next(r for r in at.radio if r.key == "etf_kline_period").set_value(period).run()

    def bars():
        cap = next(c.value for c in at.caption if c.value.startswith(f"{period}：每根 K 棒"))
        return int(cap.split("共 ")[1].split(" 根")[0])
    one_year = bars()
    next(r for r in at.radio if r.key == "etf_kline_range").set_value(120).run()
    assert errors(at) == []
    assert bars() < one_year


def test_etf_savings_future_end_date(use_db):
    """存股試算的結束日期可以選在最新股價之後，往後推估"""
    from datetime import date
    use_db()
    at = run_page(APP / "pages" / "6_ETF.py", etf_tab="🧮 存股試算")
    end = next(d for d in at.date_input if d.key == "etf_sv_end")
    end.set_value(date(end.value.year + 5, 1, 1)).run()
    assert errors(at) == []
    assert any(s.key == "etf_sv_growth" for s in at.selectbox)
    next(s for s in at.selectbox if s.key == "etf_sv_growth").set_value("照各檔過去的漲幅").run()
    assert errors(at) == []
    assert any("試算結果" in h.value for h in at.subheader)


@pytest.mark.parametrize("period", ["週線", "月線"])
def test_stock_card_weekly_monthly_range(use_db, period):
    """個股分析卡的週線、月線也有顯示區間（幾根 K 棒）"""
    use_db()
    at = run_page(APP / "pages" / "2_個股分析卡.py", card_stock_id="2330")
    next(r for r in at.radio if r.key == "kline_period").set_value(period).run()
    assert errors(at) == []
    first = 26 if period == "週線" else 12
    next(r for r in at.radio if r.key == f"kline_range_{period}").set_value(first).run()
    assert errors(at) == []
    assert any(f"圖上畫最近" in c.value for c in at.caption)
