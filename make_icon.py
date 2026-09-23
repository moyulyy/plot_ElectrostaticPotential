#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生成应用图标 assets/app.ico (静电势彩虹表面主题)。

需要 Pillow:  python -m pip install pillow
运行:        python make_icon.py
仅用于打包前生成一次, 程序运行时不依赖 Pillow。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

BASE = Path(__file__).resolve().parent
OUT = BASE / "assets" / "app.ico"
S = 512

# 彩虹配色 (与查看器 rainbow 方案一致)
STOPS = [(0.00, (0, 0, 255)), (0.20, (0, 255, 255)), (0.40, (0, 255, 0)),
         (0.60, (255, 255, 0)), (0.80, (255, 128, 0)), (1.00, (255, 0, 0))]


def _ramp(t: np.ndarray) -> np.ndarray:
    t = np.clip(t, 0, 1)
    out = np.zeros(t.shape + (3,), dtype=float)
    for i in range(len(STOPS) - 1):
        t0, c0 = STOPS[i]
        t1, c1 = STOPS[i + 1]
        m = (t >= t0) & (t <= t1)
        f = np.zeros_like(t)
        f[m] = (t[m] - t0) / max(t1 - t0, 1e-9)
        for k in range(3):
            out[..., k][m] = c0[k] + (c1[k] - c0[k]) * f[m]
    return out


def build() -> Image.Image:
    yy, xx = np.mgrid[0:S, 0:S].astype(float)
    cx = cy = (S - 1) / 2.0
    u = (xx - cx) / (S * 0.42)
    v = (yy - cy) / (S * 0.42)
    r = np.sqrt(u * u + v * v)

    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 深色圆角底
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22),
                        fill=(28, 28, 30, 255))

    # 彩虹球 (径向渐变 + 立体明暗)
    t = np.clip(0.5 - 0.5 * (v / 1.05) + 0.12 * u, 0, 1)
    rgb = _ramp(t)
    shade = np.clip(1.15 - 0.75 * r ** 2, 0.35, 1.25)
    rgb = np.clip(rgb * shade[..., None], 0, 255)

    alpha = np.clip((0.98 - r) * 6.0, 0, 1) * 255
    spec = np.clip(1.0 - np.sqrt((u + 0.32) ** 2 + (v + 0.34) ** 2) / 0.42, 0, 1)
    rgb = np.clip(rgb + (spec ** 2)[..., None] * 150, 0, 255)

    sphere = np.dstack([rgb, alpha]).astype(np.uint8)
    img.alpha_composite(Image.fromarray(sphere, "RGBA"))

    # 几个原子小球 (O 红 / Co 蓝 / Mn 紫) 与键
    d = ImageDraw.Draw(img)
    atoms = [((int(S * 0.30), int(S * 0.72)), (255, 60, 48)),
             ((int(S * 0.70), int(S * 0.70)), (40, 110, 255)),
             ((int(S * 0.50), int(S * 0.86)), (150, 90, 210))]
    for (ax, ay), col in atoms:
        d.line([ax, ay, int(S * 0.5), int(S * 0.5)], fill=(240, 240, 240, 230),
               width=int(S * 0.022))
    for (ax, ay), col in atoms:
        rad = int(S * 0.058)
        d.ellipse([ax - rad, ay - rad, ax + rad, ay + rad], fill=col)
        d.ellipse([ax - rad * 0.45, ay - rad * 0.55,
                   ax - rad * 0.05, ay - rad * 0.15],
                  fill=(255, 255, 255, 200))
    return img


def main():
    img = build()
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, format="ICO", sizes=sizes)
    img.resize((256, 256), Image.LANCZOS).save(OUT.with_suffix(".png"))
    print("已生成:", OUT)


if __name__ == "__main__":
    main()
