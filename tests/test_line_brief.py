"""LINE「早報」：早報照章節切成最多 4 張圖，brief.json 列出圖片路徑"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import line_brief  # noqa: E402

SAMPLE = Path(__file__).with_name("brief_sample.json")


def test_group_sections_limits():
    tops = [0, 300, 700, 1300, 1800, 2400, 3300, 4100, 4600, 5100]
    spans = line_brief.group_sections(tops, 5500)
    assert 1 <= len(spans) <= line_brief.MAX_IMAGES
    assert spans[0][0] == 0 and spans[-1][1] == 5500
    assert all(a[1] == b[0] for a, b in zip(spans, spans[1:]))  # 接得起來，不漏不重疊
    assert all(top in tops for top, _ in spans)                  # 只在章節開頭切


def test_short_page_one_image():
    assert line_brief.group_sections([0, 200, 400], 900) == [(0, 900)]


def test_build_meta(tmp_path, monkeypatch):
    def fake_render(html, outdir):
        assert "台股週報 10/31" in html and "本週回顧" in html  # 用 H 深色章節卡片排版，週報區塊也在
        return [(outdir / f"{k}.jpg", outdir / f"p{k}.jpg") for k in (1, 2, 3)]
    monkeypatch.setattr(line_brief, "render_images", fake_render)
    d = json.loads(SAMPLE.read_text(encoding="utf-8"))
    meta, images = line_brief.build(d, "https://claude.ai/artifact/x", tmp_path)
    assert meta["date"] == "2026-10-31" and meta["url"] == "https://claude.ai/artifact/x"
    assert meta["images"][0] == {"path": "brief/1.jpg", "preview": "brief/p1.jpg"}
    assert len(meta["images"]) == 3
