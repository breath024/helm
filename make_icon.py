# -*- coding: utf-8 -*-
"""HELM 로고(조타륜)를 helm.ico로 굽는다.

화면 로고는 index.html의 인라인 SVG가 원본이고, 이 파일은 **같은 도형**을
윈도우가 읽는 .ico로 옮긴 것뿐이다. 도형을 고치면 양쪽을 같이 고칠 것.

쓰는 곳: 바로가기(.lnk) 아이콘, 앱 창·작업표시줄 아이콘(webview.start(icon=...)).
다시 구울 때: python make_icon.py
"""
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).with_name("helm.ico")
COLOR = (201, 143, 90, 255)          # --accent #c98f5a
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
SS = 8                                # 슈퍼샘플링 배수 (계단 제거)

# SVG와 같은 24 단위 좌표계
RIM, HUB = 6.6, 2.3
SPOKES = [(14.6, 12, 21.6, 12), (9.4, 12, 2.4, 12),
          (12, 14.6, 12, 21.6), (12, 9.4, 12, 2.4),
          (13.84, 13.84, 18.79, 18.79), (10.16, 10.16, 5.21, 5.21),
          (13.84, 10.16, 18.79, 5.21), (10.16, 13.84, 5.21, 18.79)]


def draw(size):
    """size 픽셀짜리 RGBA 한 장. 작은 크기는 선을 굵혀야 형태가 남는다."""
    stroke = 1.6 if size >= 48 else (2.0 if size >= 24 else 2.4)
    px = size * SS
    scale = px / 24
    im = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    w = max(1, round(stroke * scale))

    for r in (RIM, HUB):
        d.ellipse([(12 - r) * scale, (12 - r) * scale,
                   (12 + r) * scale, (12 + r) * scale], outline=COLOR, width=w)
    for x1, y1, x2, y2 in SPOKES:
        d.line([x1 * scale, y1 * scale, x2 * scale, y2 * scale], fill=COLOR, width=w)
        # PIL엔 둥근 끝(round cap)이 없어서 양 끝에 원을 찍어 흉내낸다
        for x, y in ((x1, y1), (x2, y2)):
            d.ellipse([x * scale - w / 2, y * scale - w / 2,
                       x * scale + w / 2, y * scale + w / 2], fill=COLOR)

    return im.resize((size, size), Image.LANCZOS)


def main():
    imgs = [draw(s) for s in SIZES]
    imgs[-1].save(OUT, format="ICO", sizes=[(s, s) for s in SIZES],
                  append_images=imgs[:-1])
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {len(SIZES)} sizes)")


if __name__ == "__main__":
    main()
