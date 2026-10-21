"""LINE 圖文選單：建立新選單、上傳圖片、設為預設，再刪掉舊的同名選單"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import line_richmenu  # noqa: E402


class FakeHttp:
    def __init__(self):
        self.calls = []

    def _res(self, data=None, status=200):
        return type("Res", (), {"status_code": status, "text": "", "json": lambda self: data or {}})()

    def get(self, url, **kw):
        self.calls.append(("GET", url))
        return self._res({"richmenus": [{"richMenuId": "old1", "name": line_richmenu.NAME},
                                        {"richMenuId": "other", "name": "別的選單"}]})

    def post(self, url, **kw):
        self.calls.append(("POST", url))
        return self._res({"richMenuId": "new1"})

    def delete(self, url, **kw):
        self.calls.append(("DELETE", url))
        return self._res()


def test_set_richmenu():
    http = FakeHttp()
    assert line_richmenu.run("token", http=http) == "new1"
    urls = [f"{m} {u}" for m, u in http.calls]
    assert any(u.endswith("/richmenu/new1/content") for u in urls)
    assert any(u.endswith("/user/all/richmenu/new1") for u in urls)
    assert urls[-1].endswith("/richmenu/old1") and not any(u.endswith("/richmenu/other") for u in urls)


def test_menu_layout():
    body = line_richmenu.menu_body()
    areas = body["areas"]
    assert sum(a["bounds"]["width"] for a in areas) == line_richmenu.WIDTH
    assert [a["action"]["text"] for a in areas] == ["自選", "台積電", "說明"]
    from PIL import Image
    assert Image.open(line_richmenu.IMAGE).size == (line_richmenu.WIDTH, line_richmenu.HEIGHT)
