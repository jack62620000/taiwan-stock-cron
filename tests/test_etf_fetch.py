"""
ETF 資料抓取（etf_fetch.py）：用 2026-10-28 實際抓到的回應（只留幾檔）檢查解析結果。
不連網路。來源改版、欄位位置變了時，程式要拒絕寫入，而不是存進錯的數字。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import etf_fetch as f  # noqa: E402
import etf_update as u  # noqa: E402

DAY = "2026-10-28"

TWSE_PRICE = {
    "stat": "OK", "date": "20261028",
    "tables": [{
        "title": "115年10月28日 每日收盤行情(ETF)",
        "fields": ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價",
                   "漲跌(+/-)", "漲跌價差", "最後揭示買價", "最後揭示買量", "最後揭示賣價", "最後揭示賣量", "本益比"],
        "data": [
            ["0050", "元大台灣50", "53,628,148", "58,468", "5,807,580,332", "108.35", "108.65", "107.80", "108.65",
             "<p style= color:red>+</p>", "2.05", "108.65", "60", "108.70", "541", "0.00"],
            ["00632R", "元大台灣50反1", "97,529,880", "7,555", "946,494,112", "9.71", "9.75", "9.67", "9.67",
             "<p style= color:green>-</p>", "0.20", "9.67", "9,011", "9.68", "388", "0.00"],
        ],
    }],
}

TPEX_PRICE = {
    "date": "20261028",
    "tables": [{
        "fields": ["代號", "名稱", "收盤 ", "漲跌", "開盤 ", "最高 ", "最低", "成交股數  ", " 成交金額(元)", " 成交筆數 ",
                   "最後買價", "最後買量<br>(張數)", "最後賣價", "最後賣量<br>(張數)", "發行股數 ", "次日漲停價 ",
                   "次日跌停價"],
        "data": [
            ["00679B", "元大美債20年", "26.17", "-0.20", "26.22", "26.23", "26.06", "23,395,000", "611,978,050",
             "2,309", "26.16", "41", "26.17", "1,261", "6,131,192,000", "9,999.95", "0.01"],
            ["6488", "環球晶", "882.00", "+17.00", "865.00", "883.00", "838.00", "11,582,000", "1", "1", "", "", "",
             "", "", "", ""],
        ],
    }],
}

MIS = {"a1": [{"msgArray": [
    {"a": "0050", "b": "元大台灣50", "c": 23267000000, "d": 23500000, "e": 108.65, "f": 108.65, "g": 0.0,
     "h": "106.5300", "i": "20261028", "j": "13:30:45", "k": "1"},
    {"a": "00878", "b": "國泰永續高股息(基金之配息來源可能為收益平準金且並無保證收益及配息)", "c": "19001290000",
     "d": "21500000", "e": "35.65", "f": "35.64", "g": "0.03", "h": "35.36", "i": "20261028", "j": "16:59:52",
     "k": "1"},
    {"a": "00937B", "b": "群益ESG投等債20+", "c": "16,929,805,000", "d": "-77,500,000", "e": "14.17",
     "f": "14.2157", "g": "-0.32", "h": "-", "i": "20261028", "j": "17:00:00", "k": "3"},
]}]}

T86_FIELDS = ["證券代號", "證券名稱", "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)",
              "外陸資買賣超股數(不含外資自營商)", "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數",
              "投信買進股數", "投信賣出股數", "投信買賣超股數", "自營商買賣超股數", "自營商買進股數(自行買賣)",
              "自營商賣出股數(自行買賣)", "自營商買賣超股數(自行買賣)", "自營商買進股數(避險)", "自營商賣出股數(避險)",
              "自營商買賣超股數(避險)", "三大法人買賣超股數"]
T86 = {"stat": "OK", "fields": T86_FIELDS, "data": [
    ["0050", "元大台灣50      ", "5,627,177", "12,703,512", "-7,076,335", "0", "0", "0", "2,310,000", "0",
     "2,310,000", "-2,158,990", "904,000", "533,500", "370,500", "9,378,489", "11,907,979", "-2,529,490",
     "-6,925,325"],
]}

TPEX_INS_FIELDS = ["代號", "名稱"] + ["買進股數", "賣出股數", "買賣超股數"] * 7 + ["三大法人買賣超股數合計"]
TPEX_INS = {"date": "20261028", "tables": [{"fields": TPEX_INS_FIELDS, "data": [
    ["00679B", "元大美債20年", "2,515,126", "2,333,250", "181,876", "0", "0", "0", "2,515,126", "2,333,250",
     "181,876", "0", "0", "0", "0", "0", "0", "10,305,423", "833,549", "9,471,874", "10,305,423", "833,549",
     "9,471,874", "9,653,750"],
]}]}

ETF_DIV = {
    "fields": ["證券代號", "證券簡稱", "除息交易日", "收益分配基準日", "收益分配發放日",
               "收益分配金額 (每1受益權益單位)", "收益分配標準 (102年度起啟用)", "公告年度"],
    "data": [
        ["0056", "元大高股息", "115年10月22日", "115年10月28日", "115年11月11日", "1.72", "（規則）", 115],
        ["00400A", "主動國泰動能高息", "115年11月06日", "115年11月14日", "115年12月02日", None, "（規則）", 115],
    ],
}

SITCA_HTML = """<table><tr><td>序號</td><td>類型代號</td><td>證券代號</td><td>證券簡稱</td><td>基金統編</td>
<td>基金名稱</td><td>類型</td><td>計價幣別</td><td>基金規模(台幣)</td><td>基金規模(原幣)</td><td>基金規模市場占有率</td>
<td>單位淨值(台幣)</td><td>單位淨值(原幣)</td><td>受益權單位數</td><td>自然人受益人數</td><td>法人受益人數</td>
<td>總受益人數</td><td>本月申購總金額(台幣)</td><td>本月申購總金額(原幣)</td><td>本月買回總金額(台幣)</td>
<td>本月買回總金額(原幣)</td><td>本月定期定額扣款筆數</td><td>本月定期定額扣款戶數</td>
<td>本月定期定額扣款金額(台幣)</td><td>本月定期定額扣款金額(原幣)</td></tr>
<tr><td>340</td><td>AH11</td><td>0050</td><td>元大台灣50</td><td>98764389</td><td>元大台灣卓越50基金</td>
<td>不分級別/分配(半年配)</td><td>TWD</td><td>2,469,507,995,378</td><td>0</td><td>14.74%</td><td>111.6600</td><td>0</td>
<td>22,115,500,000</td><td>3,790,524</td><td>4,353</td><td>3,794,877</td><td>13,478,450,000</td><td>0</td>
<td>38,982,280,000</td><td>0</td><td>2,069,169</td><td>1,275,993</td><td>12,507,311,540</td><td>0</td></tr>
<tr><td>1</td><td>AH22</td><td>-</td><td>-</td><td>42461573</td><td>元大美國政府20年期(以上)債券基金</td>
<td>不分級別/分配(季配)</td><td>TWD</td><td>152,569,499,491</td><td>0</td><td>0.91%</td><td>24.5281</td><td>0</td>
<td>6,220,192,000</td><td>242,338</td><td>785</td><td>243,123</td><td>1,461,225,815</td><td>0</td><td>1,751,884,882</td>
<td>0</td><td>25,986</td><td>11,489</td><td>60,146,034</td><td>0</td></tr></table>"""

TDCC_CSV = "﻿資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n" + "\n".join(
    f"20261022,0050  ,{lv},{n},{s},{p}" for lv, n, s, p in [
        (1, 1589514, 469202978, 2.03), (2, 1392908, 3174294693, 13.76), (3, 348942, 2561632951, 11.11),
        (4, 141476, 1750896290, 7.59), (5, 77190, 1363344360, 5.91), (6, 72019, 1774112554, 7.69),
        (7, 34402, 1201682555, 5.21), (8, 19053, 860263936, 3.73), (9, 31281, 2145297240, 9.30),
        (10, 10059, 1349682620, 5.85), (11, 2619, 701737645, 3.04), (12, 472, 227538811, 0.98),
        (13, 138, 95698629, 0.41), (14, 65, 58675695, 0.25), (15, 276, 5320940727, 23.07), (16, 1, 1684, 0.00),
        (17, 3720414, 23055000000, 100.00)]
) + "\n20261022,2330  ,15,1000,1,50.00\n"


def test_codes_and_dates():
    assert [f.is_etf_code(c) for c in ["0050", "006208", "00679B", "00400A", "00632R", "2330", "02001L"]] == \
        [True, True, True, True, True, False, False]
    assert f.roc_to_iso("1151027") == "2026-10-27"
    assert f.roc_to_iso("115年10月22日") == "2026-10-22"
    assert f.roc_to_iso("115/10/28") == "2026-10-28"
    assert f.roc_to_iso("-") is None
    assert f.category_of("0050", "國內成分證券指數股票型基金") == "國內股票"
    assert f.category_of("00679B") == "債券"
    assert f.category_of("00632R", "槓桿/反向指數股票型基金") == "槓桿反向"
    assert f.category_of("00400A", "國內成分證券主動式交易所交易基金(股票)") == "主動式"


def test_twse_prices():
    r = {x["etf_id"]: x for x in f.parse_twse_prices(TWSE_PRICE, DAY)}
    assert r["0050"] == {"etf_id": "0050", "date": DAY, "open_price": 108.35, "high_price": 108.65,
                         "low_price": 107.8, "close_price": 108.65, "change": 2.05, "volume": 53628148,
                         "trade_value": 5807580332, "transactions": 58468}
    assert r["00632R"]["change"] == -0.2


def test_tpex_prices_only_etfs():
    rows = f.tpex_price_rows(TPEX_PRICE, DAY)
    r = f.parse_tpex_prices(rows, DAY)
    assert [x["etf_id"] for x in r] == ["00679B"]
    assert r[0]["close_price"] == 26.17 and r[0]["change"] == -0.2 and r[0]["volume"] == 23395000
    assert f.tpex_etf_list(rows, DAY)[0] == {"etf_id": "00679B", "etf_name": "元大美債20年", "market": "上櫃",
                                             "category": "債券", "updated_at": DAY}


def test_tpex_wrong_date_is_skipped():
    assert f.tpex_price_rows({**TPEX_PRICE, "date": "20261027"}, DAY) == []


def test_changed_columns_are_rejected():
    changed = {**TWSE_PRICE, "tables": [{**TWSE_PRICE["tables"][0],
                                         "fields": ["證券代號", "證券名稱", "成交金額"] + TWSE_PRICE["tables"][0]["fields"][3:]}]}
    assert f.parse_twse_prices(changed, DAY) == []


def test_mis_nav():
    rows, prev = f.parse_mis_nav(MIS)
    r = {x["etf_id"]: x for x in rows}
    assert r["0050"] == {"etf_id": "0050", "date": DAY, "market_price": 108.65, "est_nav": 108.65,
                         "premium_pct": 0.0, "units": 23267000000, "units_change": 23500000}
    assert r["00937B"]["units"] == 16929805000 and r["00937B"]["units_change"] == -77500000
    assert prev == {"0050": 106.53, "00878": 35.36}
    assert f.clean_name(MIS["a1"][0]["msgArray"][1]["b"]) == "國泰永續高股息"


def test_institutional():
    twse = f.parse_twse_ins(T86, DAY)[0]
    assert twse == {"etf_id": "0050", "date": DAY, "foreign_net_shares": -7076335, "sitca_net_shares": 2310000,
                    "dealers_net_shares": -2158990}
    tpex = f.parse_tpex_ins(TPEX_INS, DAY)[0]
    assert tpex == {"etf_id": "00679B", "date": DAY, "foreign_net_shares": 181876, "sitca_net_shares": 0,
                    "dealers_net_shares": 9471874}


def test_dividends_merge_keeps_known_amount():
    div = f.parse_twse_etf_div(ETF_DIV)
    notice = f.parse_twse_exdiv_notice([
        {"Date": "1151106", "Code": "00400A", "Exdividend": "息", "CashDividend": "0.150000"},
        {"Date": "1151022", "Code": "0056", "Exdividend": "息", "CashDividend": ""},
        {"Date": "1151030", "Code": "2330", "Exdividend": "息", "CashDividend": "5.0"},
    ])
    tpex = f.parse_tpex_exdiv_notice([{"ExRrightsExDividendDate": "1151019", "SecuritiesCompanyCode": "00937B",
                                       "ExRrightsExDividend": "除息", "CashDividend": "0.07200000"}])
    r = {(x["etf_id"], x["ex_date"]): x for x in f.merge_dividends(div, notice, tpex)}
    assert r[("0056", "2026-10-22")] == {"etf_id": "0056", "ex_date": "2026-10-22", "record_date": "2026-10-28",
                                          "pay_date": "2026-11-11", "cash_dividend": 1.72}
    assert r[("00400A", "2026-11-06")]["cash_dividend"] == 0.15
    assert r[("00937B", "2026-10-19")]["cash_dividend"] == 0.072
    assert len(r) == 3


def test_sitca_monthly_maps_old_rows_by_tax_id():
    rows, tax_ids = f.parse_sitca_monthly(SITCA_HTML, "2026-09", {"42461573": "00679B"})
    r = {x["etf_id"]: x for x in rows}
    assert r["0050"]["aum"] == 2469507995378 and r["0050"]["holders_total"] == 3794877
    assert r["0050"]["dca_accounts"] == 1275993 and r["0050"]["nav"] == 111.66
    assert r["00679B"]["holders_natural"] == 242338
    assert tax_ids == {"0050": "98764389", "00679B": "42461573"}
    # 沒有統編對照時，沒有代號的列略過
    assert [x["etf_id"] for x in f.parse_sitca_monthly(SITCA_HTML, "2026-09")[0]] == ["0050"]


def test_tdcc_summary():
    rows = f.parse_tdcc_csv(TDCC_CSV)
    assert rows == [{"etf_id": "0050", "week_date": "2026-10-22", "total_holders": 3720414,
                     "big_holder_ratio": 24.71, "small_holder_ratio": 40.4}]


def test_tdcc_query_page():
    html = "".join(f"<tr><td>{lv}</td><td>x</td><td>{n:,}</td><td>1</td><td>{p}</td></tr>"
                   for lv, n, p in [(1, 100, 10.0), (5, 50, 5.5), (12, 2, 30.0), (15, 1, 20.0)])
    assert f.parse_tdcc_query(html) == {"total_holders": 153, "big_holder_ratio": 50.0, "small_holder_ratio": 15.5}


def test_save_drops_incomplete_rows():
    from fake_supabase import FakeClient
    f._client = FakeClient()
    saved, failed = f.save("etf_daily_prices", [{"etf_id": "0050", "date": DAY, "close_price": 1},
                                                {"etf_id": "0050", "date": DAY, "close_price": 2},
                                                {"etf_id": "", "date": DAY}])
    assert (saved, failed) == (1, 0)
    assert f._client.tables["etf_daily_prices"] == [{"etf_id": "0050", "date": DAY, "close_price": 2}]


def test_recent_months():
    from datetime import date
    assert u.recent_months(2, date(2026, 1, 12)) == ["2025-12", "2025-11"]
    assert u.month_range("2025-11-03", "2026-02-01") == ["2025-11", "2025-12", "2026-01", "2026-02"]


def test_cleanup_archives_then_deletes_expired_days():
    """價量保留 3 年、法人 2 年：過期的日子先封存到 Storage 再刪除，沒過期的不動"""
    import gzip
    from fake_supabase import FakeClient
    client = FakeClient({
        "etf_daily_prices": [{"etf_id": e, "date": d, "close_price": 1.0}
                             for d in ("2023-10-27", "2023-10-30") for e in ("0050", "00679B")],
        "etf_ins_daily": [{"etf_id": "0050", "date": d, "foreign_net_shares": 1}
                          for d in ("2024-10-25", "2024-10-28")],
        "etf_nav": [], "etf_holder_distribution": [],
    })
    f._client = client
    try:
        assert u.cleanup_expired(__import__("datetime").date(2026, 10, 28)) == []
    finally:
        f._client = None
    assert {r["date"] for r in client.tables["etf_daily_prices"]} == {"2023-10-30"}
    assert {r["date"] for r in client.tables["etf_ins_daily"]} == {"2024-10-28"}
    files = client.storage.buckets["archive"]
    assert set(files) == {"etf_daily_prices/2023/2023-10-27.csv.gz", "etf_ins_daily/2024/2024-10-25.csv.gz"}
    assert gzip.decompress(files["etf_daily_prices/2023/2023-10-27.csv.gz"]).decode().count("\n") == 3


def test_cleanup_keeps_data_when_archive_fails(monkeypatch):
    from fake_supabase import FakeClient
    client = FakeClient({"etf_daily_prices": [{"etf_id": "0050", "date": "2020-01-02"}],
                         "etf_nav": [], "etf_ins_daily": [], "etf_holder_distribution": []})
    f._client = client

    def broken(*_, **__):
        raise RuntimeError("上傳失敗")
    monkeypatch.setattr(f, "archive_day", broken)
    try:
        problems = u.cleanup_expired(__import__("datetime").date(2026, 10, 28))
    finally:
        f._client = None
    assert problems and "etf_daily_prices" in problems[0]
    assert len(client.tables["etf_daily_prices"]) == 1


def test_backfill_ins_only_skips_prices(monkeypatch):
    calls = []
    monkeypatch.setattr(f, "trading_days", lambda s, e: ["2025-01-02"])
    monkeypatch.setattr(f, "count_rows", lambda table, **_: 0)
    monkeypatch.setattr(u, "fetch_prices_and_ins", lambda d, **kw: calls.append((d, kw)) or (0, 0, []))
    u.backfill_daily("2025-01-01", "2025-01-03", float("inf"), with_prices=False, with_ins=True)
    assert calls == [("2025-01-02", {"with_prices": False, "with_ins": True})]


TWSE_MARGIN = {
    "stat": "OK",
    "tables": [{"title": "115年10月28日 信用交易統計", "fields": ["項目"], "data": []},
               {"title": "115年10月28日 融資融券彙總 (ETF)",
                "fields": ["代號", "名稱", "買進", "賣出", "現金償還", "前日餘額", "今日餘額", "次一營業日限額",
                           "買進", "賣出", "現券償還", "前日餘額", "今日餘額", "次一營業日限額", "資券互抵", "註記"],
                "data": [["00400A", "主動國泰動能高息", "268", "209", "2", "14,475", "14,532", "533,160",
                          "0", "0", "0", "0", "0", "533,160", "6", " "]]}],
}
TPEX_MARGIN = {
    "date": "20261028",
    "tables": [{"fields": ["代號", "名稱", "前資餘額(張)", "資買", "資賣", "現償", "資餘額", "資屬證金", "資使用率(%)",
                           "資限額", "前券餘額(張)", "券賣", "券買", "券償", "券餘額", "券屬證金", "券使用率(%)",
                           "券限額", "資券相抵(張)", "備註"],
                "data": [["00679B", "元大美債20年", "4,725", "41", "107", "1", "4,658", "31", "0.3", "1,530,173",
                          "35", "0", "0", "0", "35", "0", "0.0", "1,530,173", "0", ""],
                         ["6488", "環球晶", "1", "0", "0", "0", "1", "0", "0.1", "9", "0", "0", "0", "0", "0", "0",
                          "0.0", "9", "0", ""]]}],
}


def test_margin():
    twse = f.parse_twse_margin(TWSE_MARGIN, DAY)
    assert twse == [{"etf_id": "00400A", "date": DAY, "margin_balance": 14532, "margin_limit": 533160,
                     "margin_rate": 2.73, "short_balance": 0, "short_rate": 0.0}]
    tpex = f.parse_tpex_margin(TPEX_MARGIN, DAY)
    assert tpex == [{"etf_id": "00679B", "date": DAY, "margin_balance": 4658, "margin_limit": 1530173,
                     "margin_rate": 0.3, "short_balance": 35, "short_rate": 0.0}]
    assert f.parse_tpex_margin({**TPEX_MARGIN, "date": "20261027"}, DAY) == []


SITCA_TOP10_HTML = """<table><tr><td>基金名稱</td><td>名次</td><td>標的種類</td><td>標的代號 或 Bloomberg Isin Code</td>
<td>標的名稱</td><td>金額</td><td>擔保機構**</td><td>次順位債券**</td><td>受益權單位數***</td>
<td>占基金淨資產價值 之比例(%)</td></tr>
<tr><td rowspan="11">元大台灣卓越50基金</td><td>1</td><td>國內上市</td><td>2330</td><td>台積電</td>
<td>1,488,000,000,000</td><td></td><td></td><td>0</td><td>61.20</td></tr>
<tr><td>2</td><td>國內上市</td><td>2317</td><td>鴻海</td><td>110,000,000,000</td><td></td><td></td><td>0</td><td>4.52</td></tr>
<tr><td>合計</td><td>65.72</td></tr>
<tr><td>元大優息10年期以上美國政府債券ETF基金 (本基金之配息來源可能為收益平準金且並無保證收益及配息)</td>
<td>1</td><td>政府公債 無擔保</td><td>US912810TW80</td><td>US TREASURY N/B 4.75% 11/15/2043</td>
<td>135,417,597</td><td></td><td></td><td>0</td><td>8.80</td></tr>
<tr><td>不是ETF的基金</td><td>1</td><td>國內上市</td><td>2454</td><td>聯發科</td><td>1</td><td></td><td></td><td>0</td>
<td>1.00</td></tr></table>"""


def test_top10_maps_fund_names_to_codes():
    names = f.parse_sitca_names(SITCA_HTML, {"42461573": "00679B"})
    assert names["元大台灣卓越50基金"] == "0050"
    assert names["元大美國政府20年期(以上)債券基金"] == "00679B"
    names["元大優息10年期以上美國政府債券ETF基金"] = "00968B"
    rows, unknown = f.parse_sitca_top10(SITCA_TOP10_HTML, "2026-09", names)
    assert [(r["etf_id"], r["rank"], r["holding_code"]) for r in rows] == [
        ("0050", 1, "2330"), ("0050", 2, "2317"), ("00968B", 1, "US912810TW80")]
    assert rows[0]["amount"] == 1488000000000 and rows[0]["weight_pct"] == 61.2
    assert unknown == {"不是ETF的基金"}


def test_top10_rejects_changed_columns():
    html = SITCA_TOP10_HTML.replace("<td>名次</td>", "<td>排名方式</td>")
    assert f.parse_sitca_top10(html, "2026-09", {"元大台灣卓越50基金": "0050"}) == ([], set())
