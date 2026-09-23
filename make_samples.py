#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生成内置样例数据 samples/LOCPOT 与 samples/CHGCAR (降采样, 体积小)。

从 test/ 的完整文件读取后按 factor 降采样再写成 VASP 格式, 便于随程序打包。
只需在打包前生成一次。

用法:
    python make_samples.py [source_dir] [factor]
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np

import vasp_io as V

BASE = Path(__file__).resolve().parent


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE / "test"
    factor = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    out = BASE / "samples"
    out.mkdir(exist_ok=True)

    pot_path = next((src / n for n in ("LOCPOT", "POT") if (src / n).is_file()),
                    None)
    dens_path = next((src / n for n in ("CHGCAR", "CHG") if (src / n).is_file()),
                     None)
    if pot_path is None:
        raise SystemExit(f"未在 {src} 找到 LOCPOT/POT")

    st, _, pot = V.read_vasp_volume(str(pot_path))
    V.write_vasp_volume(out / "LOCPOT", st, V.downsample(pot, factor),
                        title="ESP sample LOCPOT")
    print("写出", out / "LOCPOT", V.downsample(pot, factor).shape)

    if dens_path is not None:
        _, _, chg = V.read_vasp_volume(str(dens_path))
        V.write_vasp_volume(out / "CHGCAR", st, V.downsample(chg, factor),
                            title="ESP sample CHGCAR")
        print("写出", out / "CHGCAR", V.downsample(chg, factor).shape)

    concar = src / "CONTCAR"
    if concar.is_file():
        shutil.copy(concar, out / "CONTCAR")

    total = sum(f.stat().st_size for f in out.iterdir() if f.is_file())
    print(f"samples/ 共 {total/1e6:.2f} MB")


if __name__ == "__main__":
    main()
