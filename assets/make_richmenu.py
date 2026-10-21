"""
LINE 圖文選單的圖片。改按鈕文字時改這裡重新產生：
    pip install pillow && python assets/make_richmenu.py
會產生兩張：
    assets/line_richmenu.png：完整四格（自選股、葛蘭碧、動能股、使用說明），給有對應網頁帳號的人
    assets/line_richmenu_basic.png：三格（沒有自選股），所有人的預設選單
需要中文字型（預設用文泉驛正黑，路徑不同時改 FONT）。按鈕對應的文字在 line_richmenu.py 的 FULL_AREAS／BASIC_AREAS。
"""
from PIL import Image, ImageDraw, ImageFont
W, H = 2500, 843
FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
big, small = ImageFont.truetype(FONT, 130), ImageFont.truetype(FONT, 56)
WATCH = ("自選股", "我的自選股分析", "#dc2626")
OTHERS = [("葛蘭碧", "法則2＋急跌反彈", "#b45309"), ("動能股", "動能選股三種", "#16a34a"),
          ("使用說明", "怎麼用這個機器人", "#2563eb")]


def draw(cells, path):
    img = Image.new("RGB", (W, H), "#111827")
    d = ImageDraw.Draw(img)
    w = W // len(cells)
    for i, (t, sub, color) in enumerate(cells):
        x0 = i * w
        d.rounded_rectangle([x0 + 24, 40, x0 + w - 24, H - 40], radius=50, fill="#1f2937", outline=color, width=10)
        d.text((x0 + w / 2, H / 2 - 60), t, font=big, fill="white", anchor="mm")
        d.text((x0 + w / 2, H / 2 + 110), sub, font=small, fill="#d1d5db", anchor="mm")
    img.save(path, optimize=True)


draw([WATCH] + OTHERS, "assets/line_richmenu.png")
draw(OTHERS, "assets/line_richmenu_basic.png")
