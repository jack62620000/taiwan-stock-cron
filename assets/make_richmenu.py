"""
LINE 圖文選單的圖片（assets/line_richmenu.png）。改按鈕文字時改這裡重新產生：
    pip install pillow && python assets/make_richmenu.py
需要中文字型（預設用文泉驛正黑，路徑不同時改 FONT）。按鈕對應的文字在 line_richmenu.py 的 AREAS。
"""
from PIL import Image, ImageDraw, ImageFont
W, H = 2500, 843
FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
big, small = ImageFont.truetype(FONT, 150), ImageFont.truetype(FONT, 70)
img = Image.new("RGB", (W, H), "#111827")
d = ImageDraw.Draw(img)
cells = [("自選股", "我的自選股分析", "#dc2626"), ("台積電", "輸入代號或名稱查詢", "#b45309"), ("使用說明", "怎麼用這個機器人", "#2563eb")]
w = W // 3
for i, (t, sub, color) in enumerate(cells):
    x0 = i * w
    d.rounded_rectangle([x0 + 30, 40, x0 + w - 30, H - 40], radius=50, fill="#1f2937", outline=color, width=10)
    d.text((x0 + w / 2, H / 2 - 60), t, font=big, fill="white", anchor="mm")
    d.text((x0 + w / 2, H / 2 + 110), sub, font=small, fill="#d1d5db", anchor="mm")
img.save("assets/line_richmenu.png", optimize=True)
