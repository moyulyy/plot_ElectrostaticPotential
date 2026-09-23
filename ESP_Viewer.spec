# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 (静电势表面查看器)。

默认生成 **便携式文件夹 (onedir)**:

    dist\\ESP_Viewer\\ESP_Viewer.exe      <- 双击运行
    dist\\ESP_Viewer\\_internal\\...       <- 依赖的库/资源 (含 3dmol / samples)

把整个 `ESP_Viewer` 文件夹拷到别的 Windows 电脑即可运行 (不需要 Python)。

单文件模式: 设环境变量 ESP_ONEFILE=1 (不推荐, 启动要解包, 较慢)。

用法:
    python -m PyInstaller --noconfirm --clean ESP_Viewer.spec
或直接双击 build_exe.bat
"""

import os
import sys
from pathlib import Path

ONEFILE = os.environ.get("ESP_ONEFILE", "0").strip() == "1"

# ---------------------------------------------------------------------------
# 避免混入 base conda 的 DLL (PyInstaller 有时会把 base 的 libexpat.dll 打进来,
# 与本环境 pyexpat.pyd 版本不符, 启动即失败)。
# ---------------------------------------------------------------------------
_ENV_LIB = Path(sys.prefix) / "Library" / "bin"
_ENV_DLLS = Path(sys.prefix) / "DLLs"
_ENV_ROOT = str(Path(sys.prefix).resolve()).lower()


def _is_foreign_conda(src):
    try:
        p = Path(src).resolve()
    except OSError:
        return False
    low = str(p).lower()
    if low.startswith(_ENV_ROOT):
        return False
    return any(("miniconda" in part or "anaconda" in part)
               for part in (x.lower() for x in p.parts))


def _prefer_env_dlls(analysis):
    out, seen, fixed = [], set(), set()
    for name, src, kind in analysis.binaries:
        if _is_foreign_conda(src):
            base = Path(src).name
            for cand_dir in (_ENV_LIB, _ENV_DLLS):
                cand = cand_dir / base
                if cand.is_file():
                    src = str(cand)
                    fixed.add(base)
                    break
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((name, src, kind))
    analysis.binaries = out
    if fixed:
        print("[spec] 这些 DLL 从 base conda 改回本环境: " + ", ".join(sorted(fixed)))


# ---------------------------------------------------------------------------
# 瘦身: 去掉运行时确定用不到的 Qt 模块 / DevTools 资源 / 多余翻译。
# QtWebEngine 依赖的 QtQml/QtQuick/QtPositioning/QtWebSockets/QtPdf/opengl32sw
# 一律保留, 否则 3D 窗口黑屏或起不来。
# ---------------------------------------------------------------------------
_QT_KEEP_ALWAYS = ("Qt6WebEngine", "Qt6WebChannel", "Qt6WebSockets", "Qt6WebView")
_QT_DROP_PREFIX = (
    "Qt63D", "Qt6Charts", "Qt6DataVisualization", "Qt6Graphs", "Qt6Quick3D",
    "Qt6Multimedia", "Qt6SpatialAudio", "Qt6VirtualKeyboard", "Qt6RemoteObjects",
    "Qt6Scxml", "Qt6Sensors", "Qt6TextToSpeech", "Qt6SerialPort", "Qt6Sql",
    "Qt6Test", "Qt6QuickTest", "Qt6StateMachine", "Qt6Labs", "Qt6Concurrent",
    "Qt6Designer", "Qt6Help", "Qt6UiTools", "Qt6Bluetooth", "Qt6Nfc",
    "Qt6NetworkAuth",
)
_DROP_SUBSTR = (
    "qtwebengine_devtools_resources",   # 83 MB, 仅 DevTools 用
    "v8_context_snapshot.debug.bin",
    ".debug.pak",
)
_KEEP_QM = {"qt_zh_cn.qm", "qt_zh_tw.qm"}
_KEEP_LOCALES = {"en-us.pak", "zh-cn.pak"}


def _prune(entries):
    out, dropped = [], 0
    for item in entries:
        dest = str(item[0]).replace(chr(92), "/")
        low = dest.lower()
        base = low.rsplit("/", 1)[-1]
        if any(sub in low for sub in _DROP_SUBSTR):
            dropped += 1
            continue
        if base.endswith(".qm") and base not in _KEEP_QM:
            dropped += 1
            continue
        if "qtwebengine_locales" in low and base not in _KEEP_LOCALES:
            dropped += 1
            continue
        if base.startswith("qt6") and base.endswith(".dll"):
            if not base.startswith(tuple(x.lower() for x in _QT_KEEP_ALWAYS)) \
                    and base.startswith(tuple(x.lower() for x in _QT_DROP_PREFIX)):
                dropped += 1
                continue
        out.append(item)
    return out, dropped


datas = [
    ("3dmol", "3dmol"),        # 本地 3Dmol.js (离线可用)
    ("assets", "assets"),      # 应用图标
]
if Path("samples").is_dir():    # 内置降采样样例 (可选)
    datas.append(("samples", "samples"))
binaries = []

hiddenimports = [
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebChannel",
    "PySide6.QtNetwork",
]

EXCLUDES = [
    "tkinter", "torch", "torchvision", "torchaudio", "IPython", "jupyter",
    "notebook", "pytest", "PIL", "pandas", "matplotlib",
    "PyQt5", "PyQt6", "PySide2",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSerialPort",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner",
    "PySide6.QtHelp", "PySide6.QtUiTools", "PySide6.QtSensors",
    "PySide6.QtTextToSpeech", "PySide6.QtSpatialAudio", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtScxml", "PySide6.QtRemoteObjects",
]

block_cipher = None

a = Analysis(
    ["esp_viewer_gui.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

_prefer_env_dlls(a)
a.binaries, _n1 = _prune(a.binaries)
a.datas, _n2 = _prune(a.datas)
print(f"[spec] 瘦身: 去掉 {_n1} 个二进制, {_n2} 个数据文件")

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

_NAME = "ESP_Viewer"

if ONEFILE:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
        name=_NAME,
        debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
        runtime_tmpdir=None, console=False, disable_windowed_traceback=False,
        argv_emulation=False, target_arch=None, codesign_identity=None,
        entitlements_file=None, icon="assets/app.ico",
    )
else:
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name=_NAME,
        debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
        console=False, disable_windowed_traceback=False, argv_emulation=False,
        target_arch=None, codesign_identity=None, entitlements_file=None,
        icon="assets/app.ico",
    )
    coll = COLLECT(
        exe, a.binaries, a.zipfiles, a.datas,
        strip=False, upx=False, upx_exclude=[],
        name=_NAME,
        contents_directory="_internal",
    )
