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


def test_home_page(use_db):
    use_db()
    at = run_page(APP / "main.py")
    assert errors(at) == []


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
    for title in ["葛蘭碧分析", "每日選股評分", "動能條件", "股價走勢", "相對大盤強弱", "三大法人買賣超", "外資持股比率"]:
        assert title in heads
    assert "這頁在看什麼" in " ".join(i.value for i in at.info)
    labels = [e.label for e in at.expander]
    assert any("這頁怎麼看" in x for x in labels) and any("名詞小辭典" in x for x in labels)
    html = " ".join(str(getattr(e, "proto", "")) for e in at.main)
    assert "比 5 日前" in html or "和 5 日前差不多" in html  # 技術指標下面有和 5 日前比的變化
    assert "近 3 月年增率" in html and "券資比" in html  # 營收、資金面的延伸數據


def test_add_to_watchlist_from_stock_card(use_db):
    client = use_db()
    at = run_page(APP / "pages" / "2_個股分析卡.py", card_stock_id="1101")
    next(b for b in at.button if b.label.startswith("⭐ 加入自選股")).click().run()
    assert errors(at) == []
    assert {"group_id": 1, "stock_id": "1101"}.items() <= next(
        r for r in client.tables["watchlist"] if r["stock_id"] == "1101").items()


GRANVILLE_PAGE = next(p for p in PAGES if p.startswith("6_"))
DAILY_PAGE = next(p for p in PAGES if p.startswith("7_"))


@pytest.mark.parametrize("empty", [False, True])
def test_granville_followup_tab(use_db, empty):
    use_db(empty=empty)
    at = run_page(APP / "pages" / GRANVILLE_PAGE, granville_followup=True)
    assert errors(at) == []


def test_granville_followup_table(use_db, monkeypatch):
    """有買點時顯示卡片與表格（假資料剛好沒有法則 2 買點，借動能訊號的結果來顯示）"""
    use_db()
    import utils.analysis as analysis
    date, df = analysis.momentum_followup()
    monkeypatch.setattr(analysis, "granville_followup", lambda: (date, df.drop(columns="signal")))
    at = run_page(APP / "pages" / GRANVILLE_PAGE, granville_followup=True)
    assert errors(at) == []
    assert any("至今%" in list(d.value.columns) or "ret_now" in list(d.value.columns) for d in at.dataframe)


def test_momentum_followup_section(use_db):
    use_db()
    at = run_page(APP / "pages" / DAILY_PAGE)
    assert errors(at) == []
    assert any("ret_now" in list(d.value.columns) for d in at.dataframe)
