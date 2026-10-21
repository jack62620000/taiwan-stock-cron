"""每頁的白話說明與名詞小辭典"""


def test_every_page_guide_is_complete():
    from utils.glossary import GLOSSARY, PAGES
    for page, info in PAGES.items():
        assert info["intro"] and len(info["steps"]) >= 3, page
        missing = [t for t in info["terms"] if t not in GLOSSARY]
        assert not missing, (page, missing)
    for term, (short, detail) in GLOSSARY.items():
        assert short and detail, term


def test_every_page_calls_guide():
    from pathlib import Path
    from utils.glossary import PAGES
    app = Path(__file__).resolve().parent.parent / "app"
    used = {f.read_text(encoding="utf-8-sig").split('render_page_guide("')[1].split('"')[0]
            for f in [app / "main.py", *sorted((app / "pages").glob("*.py"))]}
    assert used == set(PAGES)
