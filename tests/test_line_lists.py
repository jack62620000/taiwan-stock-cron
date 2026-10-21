"""LINE 選單「葛蘭碧」「動能股」的回覆：欄位和網頁相同、訊息數與大小在 LINE 的限制內"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import line_cards  # noqa: E402
import line_lists  # noqa: E402


def size_ok(msgs):
    assert 1 <= len(msgs) <= 5
    assert len(msgs[0]["text"]) <= 5000
    for m in msgs[1:]:
        assert len(m["contents"]["contents"]) <= 12
        assert len(json.dumps(m["contents"], ensure_ascii=False).encode()) <= 45_000


def granville_table(n):
    from utils.analysis import BUY_COLUMNS
    rows = [{"狀態": "⏳ 等待法則2" if k % 5 else "✅ 明天開盤可買", "代號": str(2000 + k), "名稱": f"股{k}",
             "產業": "半導體業", "最新收盤": 50.0 + k, "觸發價": 51.0 + k, "距離%": 1.23, "停損價": 47.5,
             "每張風險": 3500, "比大盤%": -0.5, "趨勢": "📈 明確", "穿越均線": 2, "法則1日期": "2026-10-15",
             "說明": "明天收盤 ≥ 51.00 元（漲 1.23% 以上）就出現法則 2（還剩 15 個交易日）"} for k in range(n)]
    return pd.DataFrame(rows, columns=BUY_COLUMNS)


def test_granville_only_waiting():
    msgs = line_lists.granville_messages("2026-10-20", granville_table(10))
    size_ok(msgs)
    cards = [b for m in msgs[1:] for b in m["contents"]["contents"]]
    assert len(cards) == 8  # 只列等待法則2
    text = json.dumps(cards[0], ensure_ascii=False)
    for field in ("觸發價", "距離%", "停損價", "每張風險", "比大盤%", "穿越均線", "法則1日期", "還剩 15 個交易日"):
        assert field in text
    assert cards[0]["footer"]["contents"][0]["action"]["text"] == "2001"
    assert "欄位說明" in msgs[0]["text"]


def test_granville_many_and_empty():
    msgs = line_lists.granville_messages("2026-10-20", granville_table(100))
    size_ok(msgs)
    assert "其餘請直接輸入代號查" in msgs[0]["text"]
    empty = line_lists.granville_messages("2026-10-20", granville_table(0))
    assert len(empty) == 1 and "目前沒有" in empty[0]["text"]


def test_momentum_three_picks(use_db):
    use_db()
    mom = line_lists.load_momentum()
    date, msgs = line_lists.momentum_messages(mom)
    size_ok(msgs)
    assert date == mom["date"].max()
    for pick in line_lists.MOMENTUM_PICKS:
        assert pick in msgs[0]["text"]
    text = json.dumps(msgs[1:], ensure_ascii=False)
    for field in ("RS", "量比", "離52週高%", "趨勢模板", "新高突破", "停損價", "每張風險"):
        assert field in text


def test_lists_uploaded(use_db):
    sb = use_db()
    line_cards.run(sb)
    bucket = sb.storage.from_(line_cards.BUCKET)
    for name in ("granville.json", "momentum.json"):
        content = json.loads(bucket.download(f"{line_cards.PREFIX}/{name}"))
        assert content["messages"] and content["date"]
