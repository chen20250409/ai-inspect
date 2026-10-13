# -*- coding: utf-8 -*-
"""
生成质检测试图片（阶段 4）
=====================================================================
纯 PIL 实现，不依赖 numpy，虚拟机上装了 python3-pil 就能直接跑：
    python3 tools/gen_test_images.py

产物放在项目根目录的 testdata/ 下：
    normal01.jpg / normal02.jpg   正常样本：金属表面纹理，灰度分布均匀
    defect01.jpg                  缺陷样本：全黑（镜头遮挡 / 严重污损）
    defect02.jpg                  缺陷样本：表面划痕、杂质
    defect03.jpg                  缺陷样本：过曝反光
    defect04.jpg                  缺陷样本：成像模糊、低对比
"""
import os
import random

from PIL import Image, ImageDraw

W, H = 800, 600
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "testdata")

JPEG_QUALITY = 95


def _noise_layer(base, sigma, seed):
    """生成一张带高斯噪声的灰度图，模拟金属/塑料表面的自然纹理。"""
    random.seed(seed)
    img = Image.new("L", (W, H))
    img.putdata([
        min(255, max(0, int(random.gauss(base, sigma))))
        for _ in range(W * H)
    ])
    return img


def make_normal(seed, base=185):
    """正常样本：均匀表面 + 轻微拉丝纹理。"""
    img = _noise_layer(base, 18, seed)
    draw = ImageDraw.Draw(img)
    for y in range(0, H, 2):
        shade = random.randint(-6, 6)
        draw.line([(0, y), (W, y)], fill=min(255, max(0, base + shade)))
    return img


def make_scratch(seed, base=185, lines=10):
    """缺陷样本：在正常表面上画若干条深色划痕。"""
    img = _noise_layer(base, 18, seed)
    random.seed(seed + 1000)
    draw = ImageDraw.Draw(img)
    for _ in range(lines):
        x0 = random.randint(-50, W)
        y0 = random.randint(0, H)
        x1 = x0 + random.randint(150, 500)
        y1 = y0 + random.randint(-60, 60)
        draw.line([(x0, y0), (x1, y1)], fill=70, width=random.choice([2, 3]))
    return img


def make_black(seed=0):
    """缺陷样本：整张全黑，模拟镜头遮挡 / 产品严重发黑。"""
    return Image.new("L", (W, H), color=0)


def make_overexposed(seed, base=252):
    """缺陷样本：整体过曝，模拟强反光。"""
    return _noise_layer(base, 3, seed)


def make_blurry(seed, base=180):
    """缺陷样本：几乎无细节，模拟失焦模糊。"""
    return _noise_layer(base, 2, seed)


SAMPLES = [
    ("normal01.jpg", make_normal, dict(seed=1)),
    ("normal02.jpg", make_normal, dict(seed=2, base=178)),
    ("defect01.jpg", make_black, dict()),
    ("defect02.jpg", make_scratch, dict(seed=3)),
    ("defect03.jpg", make_overexposed, dict(seed=4)),
    ("defect04.jpg", make_blurry, dict(seed=5)),
]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print("输出目录：%s" % OUT_DIR)
    for name, fn, kwargs in SAMPLES:
        img = fn(**kwargs).convert("RGB")
        path = os.path.join(OUT_DIR, name)
        img.save(path, "JPEG", quality=JPEG_QUALITY)
        size_kb = os.path.getsize(path) / 1024.0
        print("  生成 %-14s %dx%d  %.1f KB  <- %s"
              % (name, img.width, img.height, size_kb, fn.__name__))
    print("完成，共 %d 张测试图片。" % len(SAMPLES))


if __name__ == "__main__":
    main()
