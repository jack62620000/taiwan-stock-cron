"""期貨三大法人抓取：解析期交所下載檔（不連網路，用實際抓到的 2026/10/16 資料當樣本）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auto_fetch_futures as fut  # noqa: E402

SAMPLE = """日期,商品名稱,身份別,多方交易口數,多方交易契約金額(千元),空方交易口數,空方交易契約金額(千元),多空交易口數淨額,多空交易契約金額淨額(千元),多方未平倉口數,多方未平倉契約金額(千元),空方未平倉口數,空方未平倉契約金額(千元),多空未平倉口數淨額,多空未平倉契約金額淨額(千元)
2026/10/16,臺股期貨,自營商,7101,67187113,10778,101982963,-3677,-34795850,7760,73532206,4563,43229588,3197,30302618
2026/10/16,臺股期貨,投信,9852,93574229,6317,59920921,3535,33653307,85208,806299547,2744,25949459,82464,780350088
2026/10/16,臺股期貨,外資及陸資,67924,641856428,64083,605737400,3841,36119029,9812,92925720,103487,979563802,-93675,-886638082
2026/10/16,小型臺指期貨,外資及陸資,1,1,1,1,0,0,1,1,1,1,0,0
"""


def test_parse_download_csv():
    recs = fut.parse_download_csv(SAMPLE)
    assert len(recs) == 3
    foreign = next(r for r in recs if r["item"] == "外資及陸資")
    assert foreign == {
        "date": "2026-10-16", "contract_code": "臺股期貨", "item": "外資及陸資",
        "long_oi": 9812, "long_oi_value": 92925720, "short_oi": 103487, "short_oi_value": 979563802,
        "net_oi": -93675, "net_oi_value": -886638082,
    }


def test_parse_download_rejects_other_format():
    # 期交所還沒公布時回傳的錯誤網頁，或改版後欄位對不上
    assert fut.parse_download_csv('<html><script>alert("DateTime error");</script></html>') is None
    assert fut.parse_download_csv("日期,商品名稱,身份別\n2026/10/16,臺股期貨,投信\n") is None


def test_fallback_to_openapi(monkeypatch):
    monkeypatch.setattr(fut, "fetch_from_download", lambda: [])
    monkeypatch.setattr(fut, "fetch_from_openapi", lambda: [{"date": "2026-10-16"}])
    assert fut.fetch_futures_institutional() == [{"date": "2026-10-16"}]


def test_download_retries_until_yesterday(monkeypatch):
    """查詢範圍包含今天、期交所還沒公布時會回錯誤網頁，要改查到昨天"""
    calls = []

    class Resp:
        def __init__(self, body):
            self.content = body.encode("cp950")

        def raise_for_status(self):
            pass

    def fake_post(url, data, **_):
        calls.append(data["queryEndDate"])
        return Resp("<html>DateTime error</html>" if len(calls) == 1 else SAMPLE)

    monkeypatch.setattr(fut.requests, "post", fake_post)
    assert len(fut.fetch_from_download()) == 3
    assert len(calls) == 2 and calls[0] > calls[1]
