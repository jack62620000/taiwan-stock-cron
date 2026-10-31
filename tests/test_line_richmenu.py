"""LINE 圖文選單：一般選單設為預設、完整選單掛別名並給自己，再刪掉舊的同名選單"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import line_richmenu  # noqa: E402


class FakeHttp:
    def __init__(self, alias_exists=True):
        self.calls = []
        self.alias_exists = alias_exists
        self.created = 0

    def _res(self, data=None, status=200):
        return type("Res", (), {"status_code": status, "text": "", "json": lambda self: data or {}})()

    def get(self, url, **kw):
        self.calls.append(("GET", url))
        return self._res({"richmenus": [{"richMenuId": "old1", "name": line_richmenu.FULL_NAME},
                                        {"richMenuId": "old2", "name": line_richmenu.BASIC_NAME},
                                        {"richMenuId": "other", "name": "別的選單"}]})

    def post(self, url, **kw):
        self.calls.append(("POST", url))
        if url.endswith("/richmenu"):
            self.created += 1
            return self._res({"richMenuId": f"new{self.created}"})
        if url.endswith(f"/richmenu/alias/{line_richmenu.FULL_ALIAS}") and not self.alias_exists:
            return self._res(status=404)
        return self._res()

    def delete(self, url, **kw):
        self.calls.append(("DELETE", url))
        return self._res()


def test_set_richmenus():
    http = FakeHttp()
    assert line_richmenu.run("token", "Uowner", http=http) == ("new1", "new2")
    urls = [u for _, u in http.calls]
    assert any(u.endswith("/richmenu/new1/content") for u in urls)
    assert any(u.endswith("/richmenu/new2/content") for u in urls)
    assert any(u.endswith("/user/all/richmenu/new1") for u in urls)          # 預設是一般選單
    assert any(u.endswith("/user/Uowner/richmenu/new2") for u in urls)       # 自己是完整選單
    assert not any(u.endswith("/richmenu/alias") for u in urls)              # 別名已存在就更新，不重建
    deleted = [u for m, u in http.calls if m == "DELETE"]
    assert sorted(u.rsplit("/", 1)[1] for u in deleted) == ["old1", "old2"]
    assert http.calls.index(next(c for c in http.calls if c[0] == "DELETE")) > \
        http.calls.index(("POST", f"{line_richmenu.API}/richmenu/alias/{line_richmenu.FULL_ALIAS}"))


def test_create_alias_and_no_owner():
    http = FakeHttp(alias_exists=False)
    line_richmenu.run("token", None, http=http)
    urls = [u for _, u in http.calls]
    assert any(u.endswith("/richmenu/alias") for u in urls)
    assert not any("/user/U" in u for u in urls)


def test_menu_layout():
    from PIL import Image
    for name, areas, image in [(line_richmenu.FULL_NAME, line_richmenu.FULL_AREAS, line_richmenu.FULL_IMAGE),
                               (line_richmenu.BASIC_NAME, line_richmenu.BASIC_AREAS, line_richmenu.BASIC_IMAGE)]:
        body = line_richmenu.menu_body(name, areas)
        # 每排寬度剛好鋪滿、兩排高度鋪滿
        for y in {a["bounds"]["y"] for a in body["areas"]}:
            assert sum(a["bounds"]["width"] for a in body["areas"] if a["bounds"]["y"] == y) == line_richmenu.WIDTH
        assert sum({a["bounds"]["y"]: a["bounds"]["height"] for a in body["areas"]}.values()) == line_richmenu.HEIGHT
        assert [a["action"]["text"] for a in body["areas"]] == [t for row in areas for t in row]
        assert Image.open(image).size == (line_richmenu.WIDTH, line_richmenu.HEIGHT)
    assert line_richmenu.FULL_AREAS[0] == ["自選"] + line_richmenu.BASIC_AREAS[0]
    assert line_richmenu.FULL_AREAS[1] == line_richmenu.BASIC_AREAS[1]
    assert "早報" in line_richmenu.BASIC_AREAS[0]
