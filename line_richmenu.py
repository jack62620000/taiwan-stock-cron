"""
LINE 圖文選單 - 聊天室下方的按鈕（自選股、台積電、使用說明），點了就送出對應的文字給機器人

設定好 LINE 金鑰後，用 GitHub Actions 的「LINE 圖文選單（手動）」執行一次就好，之後不用再跑；
重新執行會建立新的選單並刪掉舊的同名選單。圖片在 assets/line_richmenu.png（產生方式見 assets/make_richmenu.py）。
需要 LINE_CHANNEL_ACCESS_TOKEN。
"""
import os
import sys
from pathlib import Path

import requests

API = "https://api.line.me/v2/bot"
DATA_API = "https://api-data.line.me/v2/bot"
NAME = "台股分析選單"
IMAGE = Path(__file__).resolve().parent / "assets" / "line_richmenu.png"
WIDTH, HEIGHT = 2500, 843
# 三個按鈕平分寬度，點了送出的文字（和 Edge Function line-webhook 認得的指令相同）
AREAS = ["自選", "台積電", "說明"]


def menu_body():
    w = WIDTH // len(AREAS)
    return {
        "size": {"width": WIDTH, "height": HEIGHT},
        "selected": True,  # 打開聊天室時直接展開選單
        "name": NAME,
        "chatBarText": "選單",
        "areas": [{"bounds": {"x": i * w, "y": 0, "width": w if i < len(AREAS) - 1 else WIDTH - i * w, "height": HEIGHT},
                   "action": {"type": "message", "label": text, "text": text}} for i, text in enumerate(AREAS)],
    }


def _check(res, what):
    if res.status_code != 200:
        raise RuntimeError(f"{what}失敗：{res.status_code} {res.text}")
    return res


def run(token, http=requests):
    auth = {"Authorization": f"Bearer {token}"}
    old = _check(http.get(f"{API}/richmenu/list", headers=auth, timeout=30), "讀取選單").json().get("richmenus", [])
    menu_id = _check(http.post(f"{API}/richmenu", json=menu_body(), headers=auth, timeout=30), "建立選單").json()["richMenuId"]
    _check(http.post(f"{DATA_API}/richmenu/{menu_id}/content", data=IMAGE.read_bytes(),
                     headers={**auth, "Content-Type": "image/png"}, timeout=60), "上傳圖片")
    _check(http.post(f"{API}/user/all/richmenu/{menu_id}", headers=auth, timeout=30), "設為預設選單")
    for m in old:
        if m.get("name") == NAME:
            _check(http.delete(f"{API}/richmenu/{m['richMenuId']}", headers=auth, timeout=30), "刪除舊選單")
    print(f"已設定圖文選單 {menu_id}（刪掉 {sum(m.get('name') == NAME for m in old)} 個舊的）")
    return menu_id


def main():
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        print("沒有設定 LINE_CHANNEL_ACCESS_TOKEN")
        return 1
    run(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
