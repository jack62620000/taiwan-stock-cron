"""暫時：確認證交所、櫃買指數資料欄位（開 PR 前刪掉）"""
import json, sys, time
sys.path.insert(0, ".")
import etf_fetch as f

def show(name, url):
    print(f"\n### {name}  {url}")
    try:
        d = f.get_json(url)
    except Exception as e:
        print("ERR", e); return
    if isinstance(d, list):
        print("list 筆數", len(d)); print(json.dumps(d[:3], ensure_ascii=False, indent=1)[:1500])
    else:
        print("keys", list(d)[:20], "stat", d.get("stat"))
        for t in d.get("tables", []) or []:
            print("-- table", t.get("title"), "fields", t.get("fields"), "rows", len(t.get("data") or []))
            for r in (t.get("data") or [])[:200]:
                print("   ", r)
        if "data" in d:
            print("fields", d.get("fields")); print("rows", len(d["data"])); print(d["data"][:3])
    time.sleep(3)

show("MI_INDEX IND 10/29", "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20261029&type=IND&response=json")
show("openapi MI_INDEX", "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX")
show("openapi TAI50I", "https://openapi.twse.com.tw/v1/indicesReport/TAI50I")
show("openapi MFI94U", "https://openapi.twse.com.tw/v1/indicesReport/MFI94U")
show("tpex_index", "https://www.tpex.org.tw/openapi/v1/tpex_index")
show("tpex_reward_index", "https://www.tpex.org.tw/openapi/v1/tpex_reward_index")
show("tphd_index", "https://www.tpex.org.tw/openapi/v1/tphd_index")
