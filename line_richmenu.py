"""
LINE 圖文選單 - 聊天室下方的按鈕，點了就送出對應的文字給機器人

有兩套選單：
    一般選單（四格：明日買進、均線買賣點、動能股、使用說明）：所有人的預設選單
    完整選單（五格，多了自選股）：只給有對應網頁帳號的人。
        LINE_USER_ID（你自己）在這裡直接換成完整選單；
        其他列在 Supabase LINE_WATCH_ACCOUNTS 的人，傳訊息給機器人時由 Edge Function line-webhook 自動換
        （靠選單別名 FULL_ALIAS 找到完整選單）。

設定好 LINE 金鑰後，用 GitHub Actions 的「LINE 圖文選單（手動）」執行一次就好，之後不用再跑；
重新執行會建立新的選單並刪掉舊的同名選單。圖片在 assets/（產生方式見 assets/make_richmenu.py）。
需要 LINE_CHANNEL_ACCESS_TOKEN；LINE_USER_ID 選填。
"""
import os
import sys
from pathlib import Path

import requests

API = "https://api.line.me/v2/bot"
DATA_API = "https://api-data.line.me/v2/bot"
ASSETS = Path(__file__).resolve().parent / "assets"
WIDTH, HEIGHT = 2500, 843
# 按鈕平分寬度，點了送出的文字（和 Edge Function line-webhook 認得的指令相同）
FULL_NAME, FULL_IMAGE, FULL_AREAS = ("台股分析選單", ASSETS / "line_richmenu.png",
                                     ["自選", "明日買進", "均線買賣點", "動能股", "說明"])
BASIC_NAME, BASIC_IMAGE, BASIC_AREAS = ("台股分析選單（一般）", ASSETS / "line_richmenu_basic.png",
                                        ["明日買進", "均線買賣點", "動能股", "說明"])
FULL_ALIAS = "watch-full"  # 要和 line-webhook 的 FULL_MENU_ALIAS 相同


def menu_body(name, areas):
    w = WIDTH // len(areas)
    return {
        "size": {"width": WIDTH, "height": HEIGHT},
        "selected": True,  # 打開聊天室時直接展開選單
        "name": name,
        "chatBarText": "選單",
        "areas": [{"bounds": {"x": i * w, "y": 0, "width": w if i < len(areas) - 1 else WIDTH - i * w, "height": HEIGHT},
                   "action": {"type": "message", "label": text, "text": text}} for i, text in enumerate(areas)],
    }


def _check(res, what):
    if res.status_code != 200:
        raise RuntimeError(f"{what}失敗：{res.status_code} {res.text}")
    return res


def create_menu(http, auth, name, areas, image):
    menu_id = _check(http.post(f"{API}/richmenu", json=menu_body(name, areas), headers=auth, timeout=30),
                     f"建立{name}").json()["richMenuId"]
    _check(http.post(f"{DATA_API}/richmenu/{menu_id}/content", data=image.read_bytes(),
                     headers={**auth, "Content-Type": "image/png"}, timeout=60), f"上傳{name}圖片")
    return menu_id


def set_alias(http, auth, menu_id):
    """把別名指到新的完整選單；別名還不存在就建立"""
    res = http.post(f"{API}/richmenu/alias/{FULL_ALIAS}", json={"richMenuId": menu_id}, headers=auth, timeout=30)
    if res.status_code != 200:
        _check(http.post(f"{API}/richmenu/alias", json={"richMenuAliasId": FULL_ALIAS, "richMenuId": menu_id},
                         headers=auth, timeout=30), "建立選單別名")


def run(token, owner_id=None, http=requests):
    """回傳 (一般選單 ID, 完整選單 ID)"""
    auth = {"Authorization": f"Bearer {token}"}
    old = _check(http.get(f"{API}/richmenu/list", headers=auth, timeout=30), "讀取選單").json().get("richmenus", [])
    basic_id = create_menu(http, auth, BASIC_NAME, BASIC_AREAS, BASIC_IMAGE)
    full_id = create_menu(http, auth, FULL_NAME, FULL_AREAS, FULL_IMAGE)
    _check(http.post(f"{API}/user/all/richmenu/{basic_id}", headers=auth, timeout=30), "設為預設選單")
    set_alias(http, auth, full_id)
    if owner_id:
        _check(http.post(f"{API}/user/{owner_id}/richmenu/{full_id}", headers=auth, timeout=30), "設定你的完整選單")
    stale = [m["richMenuId"] for m in old if m.get("name") in (FULL_NAME, BASIC_NAME)]
    for menu_id in stale:
        _check(http.delete(f"{API}/richmenu/{menu_id}", headers=auth, timeout=30), "刪除舊選單")
    print(f"已設定一般選單 {basic_id}、完整選單 {full_id}（刪掉 {len(stale)} 個舊的）"
          + ("" if owner_id else "；沒有設定 LINE_USER_ID，你自己的完整選單會在你傳訊息給機器人時換上"))
    return basic_id, full_id


def main():
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        print("沒有設定 LINE_CHANNEL_ACCESS_TOKEN")
        return 1
    run(token, (os.getenv("LINE_USER_ID") or "").strip() or None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
