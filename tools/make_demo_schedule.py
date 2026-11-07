"""Draw a fictional handwritten-style shift roster for the demo:
DEMO_FONT=<a Traditional Chinese .ttf/.ttc> uv run --with pillow python tools/make_demo_schedule.py"""

import json
import os
import random
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONT = os.environ.get("DEMO_FONT", "msjh.ttc")
record = json.loads((ROOT / "samples" / "lin_xiaomei.json").read_text(encoding="utf-8"))
shifts = {s["date"]: s for s in record["shifts"]}

W, H = 1400, 980
img = Image.new("RGB", (W, H), (250, 247, 238))
draw = ImageDraw.Draw(img)
title = ImageFont.truetype(FONT, 46)
head = ImageFont.truetype(FONT, 30)
cell = ImageFont.truetype(FONT, 28)
draw.text((60, 40), "好喝手搖飲 三民店　10月班表（10/5–10/18）", font=title, fill=(30, 30, 30))
draw.text((60, 110), "員工：林小美　（虛構範例）", font=head, fill=(60, 60, 60))
days = [date(2026, 10, d) for d in range(5, 19)]
labels = "一二三四五六日"
x0, y0, cw, rh = 60, 180, 182, 110
for week in range(2):
    for i in range(7):
        d = days[week * 7 + i]
        x, y = x0 + i * cw, y0 + week * (rh * 3)
        draw.rectangle([x, y, x + cw, y + rh], outline=(80, 80, 80), width=2, fill=(235, 232, 220))
        draw.text((x + 14, y + 30), f"{d.month}/{d.day}（{labels[i]}）", font=head, fill=(20, 20, 20))
        draw.rectangle([x, y + rh, x + cw, y + rh * 2], outline=(80, 80, 80), width=2)
        s = shifts.get(d.isoformat())
        text = f"{s['start']}-{s['end']}" if s else "休"
        color = (20, 40, 140) if s else (170, 30, 30)
        draw.text((x + 14 + random.randint(-3, 3), y + rh + 34 + random.randint(-3, 3)), text, font=cell, fill=color)
        if s and s.get("break_minutes"):
            draw.text((x + 14, y + rh + 72), f"休息{s['break_minutes']}分", font=ImageFont.truetype(FONT, 20), fill=(90, 90, 90))
draw.text((60, H - 70), "※ 時薪 185 元，每月 5 日發薪", font=head, fill=(60, 60, 60))
img = img.rotate(1.2, expand=False, fillcolor=(250, 247, 238)).filter(ImageFilter.GaussianBlur(0.6))
out = ROOT / "samples" / "schedule_lin_xiaomei.jpg"
img.save(out, quality=82)
print(out)
