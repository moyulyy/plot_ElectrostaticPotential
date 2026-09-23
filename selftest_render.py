#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用 Playwright (Edge/Chrome) 无头验证生成的查看器 HTML 能正常渲染。

用法:
    python selftest_render.py [LOCPOT] [下采样倍数] [CHGCAR]
"""
import base64
import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np              # noqa: E402
import vasp_io as V             # noqa: E402
import viewer3d as VW           # noqa: E402


def find_edge():
    cands = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def main():
    from playwright.sync_api import sync_playwright

    pot_path = sys.argv[1] if len(sys.argv) > 1 else "test/LOCPOT"
    factor = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    dens_path = sys.argv[3] if len(sys.argv) > 3 else None

    st, dims, data = V.read_vasp_volume(pot_path)
    down = V.downsample(data, factor)
    pot = {"header": V.cube_header(*down.shape, st.cell),
           "b64": V.volume_base64(down)}

    dens = None
    lo, hi = -12.0, 12.0
    if dens_path:
        _, _, ddata = V.read_vasp_volume(dens_path)
        vcell = abs(float(np.linalg.det(st.cell))) or 1.0
        ddown = V.downsample(ddata / vcell, factor)
        dens = {"header": V.cube_header(*ddown.shape, st.cell),
                "b64": V.volume_base64(ddown)}
        lo, hi = V.percentiles(data, [1, 99])

    settings = {
        "dens_level": 0.1, "opacity": 0.8, "smoothness": 3,
        "scheme": "rainbow", "scheme_min": -12.0, "scheme_max": 12.0,
        "scheme_colors": ["#FF0000", "#00FF00", "#0000FF"],
        "atom_style": "ballstick", "atom_scale": 1.0,
        "show_cell": True, "cell_color": "#666666", "bg": "#FFFFFF",
        "view_name": "top", "pot_sign": -1,
    }
    html = VW.build_html(st.xyz(), st.cell_edges(), pot, dens,
                         V.view_quaternions(st.cell), V.fit_sphere(st),
                         settings)
    tmp = Path(tempfile.mkdtemp())
    f = tmp / "esp.html"
    f.write_text(html, encoding="utf-8")
    print("html size", len(html), "downsample", down.shape)

    edge = find_edge()
    print("edge", edge)
    logs = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=edge, headless=True,
            args=["--use-gl=angle", "--use-angle=swiftshader",
                  "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist",
                  "--disable-gpu-sandbox"])
        page = browser.new_page(viewport={"width": 900, "height": 620})
        page.on("console", lambda m: logs.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: logs.append(f"[pageerror] {e}"))
        page.goto(f.as_uri())
        page.wait_for_function("window.ready === true", timeout=60000)
        page.wait_for_timeout(4000)
        print("count ->", page.evaluate("window.surfaceCount()"))

        for name in ["top", "front", "right", "bottom", "back", "left"]:
            page.evaluate("n => window.setNamedView(n)", name)
            page.wait_for_timeout(700)
        print("views cycled")

        page.evaluate("cfg => window.applySettings(cfg)",
                      dict(settings, dens_level=0.2, scheme="rainbow_r"))
        page.wait_for_timeout(3000)
        print("level 0.2 ->", page.evaluate("window.surfaceCount()"))

        cols = page.evaluate("window.sampleColors(48)")
        print("sampleColors:", len(cols) if cols else None, cols[:2] if cols else None)
        uri2 = page.evaluate("window.pngURI()")
        print("canvas png len", len(uri2) if uri2 else None)
        browser.close()

    print("---- console ----")
    for line in logs[-40:]:
        print(line)
    errs = [l for l in logs if "error" in l.lower() or "pageerror" in l.lower()]
    print("SELFTEST", "FAIL" if errs else "OK")


if __name__ == "__main__":
    main()
