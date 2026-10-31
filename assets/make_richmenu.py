"""
LINE 圖文選單的圖片。改按鈕文字時改這裡重新產生：
    pip install pillow && python assets/make_richmenu.py
會產生兩張：
    assets/line_richmenu.png：兩排七格（上排自選股、早報、明日買進、均線買賣點；下排動能股、大盤趨勢、使用說明），給有對應網頁帳號的人
    assets/line_richmenu_basic.png：兩排六格（上排沒有自選股），所有人的預設選單
需要中文字型（預設用文泉驛正黑，路徑不同時改 FONT）。按鈕對應的文字在 line_richmenu.py 的 FULL_AREAS／BASIC_AREAS。
"""
from PIL import Image, ImageDraw, ImageFont
W, H = 2500, 1686  # 兩排，每排高 843
FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
WATCH = ("自選股", "我的自選股", "#9333ea")
TOP = [("早報", "每日台股早報", "#e11d48"), ("明日買進", "評分前10名", "#dc2626"), ("均線買賣點", "葛蘭碧法則2", "#b45309")]
BOTTOM = [("動能股", "動能選股", "#16a34a"), ("大盤趨勢", "多空與外資", "#0891b2"), ("使用說明", "怎麼用", "#2563eb")]


def draw(rows, path):
    img = Image.new("RGB", (W, H), "#111827")
    d = ImageDraw.Draw(img)
    h = H // len(rows)
    # 最窄那排決定字的大小（兩排一樣大），最長的「均線買賣點」（5 個字）要放得下
    w_min = W // max(len(r) for r in rows)
    big = ImageFont.truetype(FONT, min(110, int((w_min - 110) / 5)))
    small = ImageFont.truetype(FONT, min(56, int((w_min - 60) / 6)))
    for r, cells in enumerate(rows):
        w, y0 = W // len(cells), r * h
        for i, (t, sub, color) in enumerate(cells):
            x0 = i * w
            d.rounded_rectangle([x0 + 16, y0 + 24, x0 + w - 16, y0 + h - 24], radius=40, fill="#1f2937", outline=color, width=10)
            d.text((x0 + w / 2, y0 + h / 2 - 60), t, font=big, fill="white", anchor="mm")
            d.text((x0 + w / 2, y0 + h / 2 + 100), sub, font=small, fill="#d1d5db", anchor="mm")
    img.save(path, optimize=True)


draw([[WATCH] + TOP, BOTTOM], "assets/line_richmenu.png")
draw([TOP, BOTTOM], "assets/line_richmenu_basic.png")
