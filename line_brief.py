"""
LINE 打「早報」的回覆 - 每日台股早報，和網頁、PDF 同一個版面，切成幾張圖片

早報由 Claude 每天早上產生（data.json 格式同 tests/brief_sample.json），
再用 GitHub Actions「LINE 早報（手動）」把 data.json 傳進來：
    1. 用 brief/build_brief.py 排版（H 深色章節卡片），照章節切成最多 4 張圖（JPEG，另存縮圖）
    2. 圖片存到 Storage：archive 桶 / _line_cards / brief / 1.jpg…、p1.jpg…（縮圖）
    3. 最後存 _line_cards / brief.json：{"date", "title", "headline", "url", "images": [{"path", "preview"}]}
LINE 機器人（supabase/functions/line-webhook）收到「早報」時，替圖片產生有期限的連結，回覆一則文字加圖片；
是回覆訊息，不算推播額度。

用法：python line_brief.py <data.json> [完整早報網址]
需要 SUPABASE_URL、SUPABASE_KEY；設 LINE_BRIEF_DRY_RUN=<資料夾> 只把圖片和 brief.json 存到那個資料夾、不上傳。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "brief"))

FILE = "brief.json"
IMAGE_DIR = "brief"
MAX_IMAGES = 4           # 一次回覆最多 5 則：1 則文字 + 4 張圖
CHUNK_CSS_PX = 1500      # 每張圖大約多高（CSS px），照章節切，手機上一張滑完剛好
WIDTH, SCALE = 1080, 1.5  # 圖片寬 1620px：放大看得清楚，檔案又不會太大
PREVIEW_WIDTH = 540


def group_sections(tops, total, max_h=CHUNK_CSS_PX, max_n=MAX_IMAGES):
    """把各章節的起點（由上到下）合成最多 max_n 段，每段盡量不超過 max_h；回傳 [(上緣, 下緣)]"""
    cuts = [0.0]
    for t in tops[1:]:
        if t - cuts[-1] > max_h:
            prev = max((x for x in tops if cuts[-1] < x < t), default=t)
            cuts.append(prev if t - prev < max_h and prev > cuts[-1] else t)
    while len(cuts) > max_n:  # 太多段：合併最矮的相鄰兩段
        spans = [(cuts[i + 1] if i + 1 < len(cuts) else total) - cuts[i] for i in range(len(cuts))]
        i = min(range(1, len(cuts)), key=lambda k: spans[k - 1] + spans[k])
        cuts.pop(i)
    return [(cuts[i], cuts[i + 1] if i + 1 < len(cuts) else total) for i in range(len(cuts))]


def render_images(html, outdir):
    """排好的早報 HTML 照章節切成 JPEG，回傳 [(原圖路徑, 縮圖路徑)]"""
    from PIL import Image
    from playwright.sync_api import sync_playwright
    out = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": 1200}, device_scale_factor=SCALE)
        page.set_content(html)
        page.wait_for_timeout(300)
        tops = page.evaluate("[...document.querySelectorAll('section')].map(e => e.getBoundingClientRect().top + scrollY)")
        total = page.evaluate("document.documentElement.scrollHeight")
        tops = [0.0] + [t - 10 for t in tops[1:]]  # 第一段從頁首開始，含標題
        for i, (top, bottom) in enumerate(group_sections(tops, total), 1):
            png = outdir / f"{i}.png"
            page.screenshot(path=str(png), full_page=True, clip={"x": 0, "y": top, "width": WIDTH, "height": bottom - top})
            img = Image.open(png).convert("RGB")
            full, prev = outdir / f"{i}.jpg", outdir / f"p{i}.jpg"
            img.save(full, "JPEG", quality=85, optimize=True)
            img.resize((PREVIEW_WIDTH, round(img.height * PREVIEW_WIDTH / img.width))).save(prev, "JPEG", quality=70)
            out.append((full, prev))
        browser.close()
    return out


def build(d, url, outdir):
    import full_layouts
    images = render_images(full_layouts.listing_cards(d), outdir)
    meta = {"date": d["date_tw"], "title": d["title"], "headline": d["headline"], "url": url or None,
            "images": [{"path": f"{IMAGE_DIR}/{f.name}", "preview": f"{IMAGE_DIR}/{p.name}"} for f, p in images]}
    return meta, images


def main():
    d = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    url = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None
    dry = os.getenv("LINE_BRIEF_DRY_RUN")
    outdir = Path(dry) if dry else Path(tempfile.mkdtemp())
    outdir.mkdir(parents=True, exist_ok=True)
    meta, images = build(d, url, outdir)
    if dry:
        (outdir / FILE).write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已存到 {outdir}（{len(images)} 張圖）")
        return
    from line_cards import BUCKET, PREFIX
    from utils.db import supabase
    bucket = supabase.storage.from_(BUCKET)
    for full, prev in images:
        for f in (full, prev):
            bucket.upload(f"{PREFIX}/{IMAGE_DIR}/{f.name}", f.read_bytes(), {"content-type": "image/jpeg", "upsert": "true"})
    data = json.dumps(meta, ensure_ascii=False, separators=(",", ":")).encode()
    bucket.upload(f"{PREFIX}/{FILE}", data, {"content-type": "application/json", "upsert": "true"})  # 圖片傳完才換
    sizes = ", ".join(f"{f.stat().st_size // 1024} KB" for f, _ in images)
    print(f"已上傳 {d['date_tw']} 早報：{len(images)} 張圖（{sizes}）")


if __name__ == "__main__":
    main()
