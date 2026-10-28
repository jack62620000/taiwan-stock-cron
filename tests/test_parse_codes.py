"""自選股一次加多檔：代號或名稱、各種分隔符號、重複、已在清單、找不到"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from utils.groups import parse_stock_codes  # noqa: E402

NAMES = {"2330": "台積電", "2317": "鴻海", "2454": "聯發科", "1101": "台泥"}


def test_separators_names_and_duplicates():
    text = " 2330，2317、聯發科\n2330;  台泥；2317 "
    assert parse_stock_codes(text, NAMES) == (["2330", "2317", "2454", "1101"], [], [])


def test_already_and_unknown():
    assert parse_stock_codes("2330 2317 9999 abc 9999", NAMES, ["2317"]) == (["2330"], ["2317"], ["9999", "abc"])
    assert parse_stock_codes("", NAMES) == ([], [], [])
    assert parse_stock_codes(None, NAMES) == ([], [], [])
