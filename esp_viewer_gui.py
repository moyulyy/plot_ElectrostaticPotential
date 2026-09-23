#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
静电势表面查看器 (ESP Surface Viewer)
====================================

本地窗口程序: 从**给定目录**自动读取 VASP 的 LOCPOT 与 CHGCAR, 用 3Dmol.js
在窗口内显示与 VESTA 参考图一致的静电势表面:

    * 电荷密度等值面 + 静电势彩虹着色 (唯一显示模式)
    * 密度等值面水平可调 (e/Å³)
    * 左侧彩条 colorbar, 区间 (电势范围 eV) 可自定义
    * 正交投影 (orthographic) + 六个标准视角
    * 不透明度 / 原子样式 / 晶胞 / 背景等显示选项

GUI 风格参考 CONT-gif 项目 (PySide6, iOS/macOS 风格)。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
from PySide6.QtCore import Qt, QRectF, QThread, QTimer, Signal
from PySide6.QtGui import (QColor, QCursor, QIcon, QImage, QLinearGradient,
                           QPainter, QPen)
from PySide6.QtWidgets import (QApplication, QColorDialog, QDoubleSpinBox,
                               QFileDialog, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QScrollArea,
                               QSizePolicy, QSlider, QStackedWidget,
                               QVBoxLayout, QWidget, QButtonGroup)

import vasp_io as V
from ui_kit import (DARK, LIGHT, THEME, Card, ComboBox, Switch, apply_theme,
                    field_label, hint_label)
from viewer3d import EspViewer, resource_dir

BASE_DIR = Path(__file__).resolve().parent

# 自适应降采样: 网格点数不超过 MAX_POINTS
MAX_POINTS = 700_000


def auto_factor(dims) -> int:
    factor, nx, ny, nz = 1, *dims
    while (nx // factor) * (ny // factor) * (nz // factor) > MAX_POINTS:
        factor += 1
    return factor

ATOM_STYLES = [("ballstick", "球棍"), ("sphere", "球体"),
               ("stick", "棍棒"), ("line", "线框"), ("none", "隐藏原子")]

SCHEMES = [("rainbow", "彩虹 (VESTA)"), ("rainbow_r", "反向彩虹"),
           ("rwb", "红-白-蓝"), ("bwr", "蓝-白-红"),
           ("roygb", "红橙黄绿蓝"), ("sinebow", "正弦彩虹"),
           ("custom", "自定义三色")]

# 六标准视角 (基于晶胞矢量, 与 VESTA 惯例一致)
VIEW_BUTTONS = [("top", "俯视"), ("front", "正视"), ("right", "右视"),
                ("bottom", "仰视"), ("back", "后视"), ("left", "左视")]

POT_NAMES = ("LOCPOT", "POT")
DENS_NAMES = ("CHGCAR", "CHG")


def app_dir() -> Path:
    """exe 所在目录 (打包后) / 项目目录 (开发时); 数据目录默认放这里。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return BASE_DIR


def default_data_dir() -> Path:
    """依次寻找: exe旁 test/ -> data/ -> 内置 samples/ -> exe旁。"""
    cands = [app_dir() / "test", app_dir() / "data",
             resource_dir() / "samples", app_dir()]
    for d in cands:
        if d.is_dir() and any((d / n).is_file() for n in POT_NAMES):
            return d
    return app_dir() / "test"


# ==========================================================================
# 小控件
# ==========================================================================
class ColorButton(QPushButton):
    """点击弹出取色器的颜色按钮。"""

    colorChanged = Signal(str)

    def __init__(self, color="#FFFFFF", parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(30)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.clicked.connect(self._pick)
        self._sync()

    def color(self) -> str:
        return self._color.name()

    def set_color(self, color: str):
        c = QColor(color)
        if c.isValid() and c.name() != self._color.name():
            self._color = c
            self._sync()
            self.colorChanged.emit(c.name())

    def _sync(self):
        c = self._color
        lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
        fg = "#111111" if lum > 150 else "#FFFFFF"
        self.setText(c.name().upper())
        self.setStyleSheet(
            f"QPushButton{{background:{c.name()};color:{fg};"
            f"border:1px solid rgba(0,0,0,0.18);border-radius:7px;"
            f"font-size:11px;font-weight:600;}}"
            f"QPushButton:hover{{border:1px solid {THEME['accent']};}}")

    def _pick(self):
        c = QColorDialog.getColor(self._color, self, "选择颜色")
        if c.isValid():
            self.set_color(c.name())


class SliderRow(QWidget):
    """标签 + 滑杆 + 数值输入 (支持对数刻度)。"""

    changed = Signal(float)

    def __init__(self, label, minv, maxv, value, decimals=3, log=False,
                 step=0.05, width=92):
        super().__init__()
        self._min, self._max, self._log = float(minv), float(maxv), log
        self._lock = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(field_label(label, width))

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setCursor(Qt.PointingHandCursor)
        lay.addWidget(self.slider, 1)

        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(decimals)
        self.spin.setRange(minv, maxv)
        self.spin.setSingleStep(step)
        self.spin.setFixedWidth(96)
        lay.addWidget(self.spin)

        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)
        self.set_value(value)

    def _val_to_pos(self, v):
        v = max(self._min, min(self._max, v))
        if self._log:
            lo, hi = np.log10(self._min), np.log10(self._max)
            return int(round((np.log10(v) - lo) / (hi - lo) * 1000))
        return int(round((v - self._min) / (self._max - self._min) * 1000))

    def _pos_to_val(self, p):
        if self._log:
            lo, hi = np.log10(self._min), np.log10(self._max)
            return float(10 ** (lo + (hi - lo) * p / 1000.0))
        return self._min + (self._max - self._min) * p / 1000.0

    def _from_slider(self, _):
        if self._lock:
            return
        self._lock = True
        self.spin.setValue(self._pos_to_val(self.slider.value()))
        self._lock = False
        self.changed.emit(self.value())

    def _from_spin(self, v):
        if self._lock:
            return
        self._lock = True
        self.slider.setValue(self._val_to_pos(v))
        self._lock = False
        self.changed.emit(self.value())

    def value(self) -> float:
        return float(self.spin.value())

    def set_value(self, v):
        self._lock = True
        self.spin.setValue(max(self._min, min(self._max, float(v))))
        self.slider.setValue(self._val_to_pos(float(v)))
        self._lock = False

    def set_range(self, minv, maxv):
        self._min, self._max = float(minv), float(maxv)
        cur = self.value()
        self._lock = True
        self.spin.setRange(minv, maxv)
        self._lock = False
        self.set_value(min(max(cur, minv), maxv))


class ColorBar(QWidget):
    """竖直彩条 (Qt 绘制, 与 3D 视图并排, 不依赖网页层叠, 稳定不闪烁)。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._colors = ["#0000FF", "#00FFFF", "#00FF00",
                        "#FFFF00", "#FF8000", "#FF0000"]
        self._vmin, self._vmax = -12.0, 12.0
        self.setFixedWidth(84)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

    def set_colors(self, colors):
        if colors:
            self._colors = list(colors)
            self.update()

    def set_range(self, vmin, vmax):
        self._vmin, self._vmax = float(vmin), float(vmax)
        self.update()

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(QPen(QColor(THEME["card_border"]), 1))
        p.setBrush(QColor(THEME["card"]))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 10, 10)

        margin, title_h, label_h, bar_w = 10, 46, 18, 22
        bar_x = (w - bar_w) / 2.0
        top = margin + title_h
        bot = h - margin - label_h
        if bot - top < 20:
            p.end()
            return

        grad = QLinearGradient(bar_x, bot, bar_x, top)   # 底=min, 顶=max
        n = max(len(self._colors) - 1, 1)
        for i, c in enumerate(self._colors):
            grad.setColorAt(i / n, QColor(c))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRect(QRectF(bar_x, top, bar_w, bot - top))
        p.setPen(QPen(QColor("#8A8A8E"), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(bar_x + 0.5, top + 0.5, bar_w - 1, bot - top - 1))

        f = self.font()
        f.setBold(True)
        f.setPointSize(9)
        p.setFont(f)
        p.setPen(QColor(THEME["text"]))
        p.drawText(QRectF(0, margin - 2, w, 16), Qt.AlignCenter, "ESP")
        f.setBold(False)
        f.setPointSize(8)
        p.setFont(f)
        p.drawText(QRectF(0, margin + 13, w, 14), Qt.AlignCenter, "eV")

        p.setPen(QColor(THEME["text_soft"]))
        p.drawText(QRectF(0, top - 17, w, 14), Qt.AlignCenter,
                   f"{self._vmax:.2f}")
        p.drawText(QRectF(0, bot + 2, w, 14), Qt.AlignCenter,
                   f"{self._vmin:.2f}")
        p.end()


# ==========================================================================
# 后台载入 (从目录自动读取 LOCPOT + CHGCAR)
# ==========================================================================
class LoadWorker(QThread):
    progress = Signal(int, int)
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, pot_path: str, dens_path: Optional[str]):
        super().__init__()
        self.pot_path = pot_path
        self.dens_path = dens_path

    def run(self):  # noqa: D401
        try:
            def cb(done, total):
                self.progress.emit(int(done), int(total))

            st, dims, data = V.read_vasp_volume(self.pot_path, cb)
            factor = auto_factor(dims)
            pot_down = V.downsample(data, factor)
            pot = {"header": V.cube_header(*pot_down.shape, st.cell),
                   "b64": V.volume_base64(pot_down)}
            amp = float(np.percentile(np.abs(data), 95)) or 1.0
            stats = {"pot_amp": amp}

            dens = None
            if self.dens_path:
                _, _, ddata = V.read_vasp_volume(self.dens_path, cb)
                vcell = abs(float(np.linalg.det(st.cell))) or 1.0
                rho = ddata / vcell                       # e/Å³
                d_down = V.downsample(rho, factor)
                dens = {"header": V.cube_header(*d_down.shape, st.cell),
                        "b64": V.volume_base64(d_down)}
                stats["rho_p95"] = float(np.percentile(rho, 95))
                # 自适应默认等值面水平: 分子(大量真空) ~0.01, 固体 ~0.1
                stats["dens_default"] = (0.1 if float((rho > 0.1).mean()) > 0.05
                                         else 0.01)
                ref = stats["dens_default"]
                band = (rho > ref * 0.5) & (rho < ref * 2.0)   # 等值面附近的电势
                if int(band.sum()) > 100:
                    pv = data[band]
                    lo = float(np.percentile(pv, 2))
                    hi = float(np.percentile(pv, 98))
                    if hi - lo < 1e-6:
                        lo, hi = -amp, amp
                    stats["esp_lo"], stats["esp_hi"] = lo, hi
                else:
                    stats["esp_lo"], stats["esp_hi"] = -amp, amp

            self.done.emit({"struct": st, "dims": dims, "factor": factor,
                            "pot": pot, "dens": dens, "stats": stats})
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{exc}\n{traceback.format_exc()}")


# ==========================================================================
# 主窗口
# ==========================================================================
class MainWindow(QWidget):
    RESIZE_MARGIN = 6
    DEFAULT_W = 1360
    DEFAULT_H = 860

    def __init__(self, data_dir: Optional[str] = None):
        super().__init__()
        self.setWindowTitle("静电势表面查看器")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setMinimumSize(1120, 700)
        self.resize(self.DEFAULT_W, self.DEFAULT_H)
        self._first_show = False
        self._dark = False
        self._switches = []
        self._color_buttons = []

        self.data: Optional[dict] = None
        self.worker: Optional[LoadWorker] = None
        self._ready = False
        self._view_name = "top"

        self._set_icon()
        self._build()
        self.edit_dir.setText(str(data_dir or default_data_dir()))
        self._refresh_detected()

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(220)
        self._debounce.timeout.connect(lambda: self._apply(0))

        QTimer.singleShot(300, self._maybe_autoload)

    # ------------------------------------------------------------------
    def _set_icon(self):
        for ico in (resource_dir() / "assets" / "app.ico",
                    BASE_DIR / "assets" / "app.ico"):
            if ico.is_file():
                self.setWindowIcon(QIcon(str(ico)))
                break

    def _maybe_autoload(self):
        pot, dens = self._detected()
        if pot:
            self._start_load()

    # ==================================================================
    # 界面
    # ==================================================================
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        self.outer = outer

        self.container = QFrame()
        self.container.setObjectName("Window")
        outer.addWidget(self.container)

        root = QVBoxLayout(self.container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_titlebar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self._build_actionbar())

        self.pages = QStackedWidget()
        self.pages.addWidget(self._page_viewer())
        self.pages.addWidget(self._page_log())
        self.pages.addWidget(self._page_about())
        right.addWidget(self.pages, 1)
        right.addWidget(self._build_footer())

        wrap = QWidget()
        wrap.setLayout(right)
        body.addWidget(wrap, 1)
        root.addLayout(body, 1)

    # ---- 阴影 ----
    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        m = 18
        if self.isMaximized() or self.isFullScreen():
            p.end()
            super().paintEvent(event)
            return
        from PySide6.QtCore import QRectF
        rect = QRectF(m - 4, m - 2, self.width() - 2 * m + 8,
                      self.height() - 2 * m + 8)
        layers = 16
        for i in range(layers, 0, -1):
            alpha = int(2.6 * (layers - i) / layers * (2.4 if self._dark else 1.7))
            grow = i * 0.9
            p.setBrush(QColor(0, 0, 0, max(0, alpha)))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(rect.adjusted(-grow, -grow + 2, grow, grow + 2),
                              16 + grow / 2, 16 + grow / 2)
        p.end()
        super().paintEvent(event)

    # ---- 标题栏 ----
    def _build_titlebar(self):
        bar = QWidget()
        bar.setObjectName("TitleBar")
        bar.setFixedHeight(48)
        bar.mousePressEvent = self._titlebar_press
        bar.mouseDoubleClickEvent = self._titlebar_double
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(8)
        for name, slot in (("TrafficClose", self.close),
                           ("TrafficMin", self.showMinimized),
                           ("TrafficMax", self._toggle_max)):
            btn = QPushButton()
            btn.setObjectName(name)
            btn.setFixedSize(12, 12)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(slot)
            lay.addWidget(btn)
        lay.addStretch(1)
        title = QLabel("静电势表面  ·  ESP Surface Viewer")
        title.setObjectName("AppName")
        lay.addWidget(title)
        lay.addStretch(1)
        self.theme_btn = QPushButton("🌙")
        self.theme_btn.setObjectName("Ghost")
        self.theme_btn.setFixedWidth(38)
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.setToolTip("切换深色 / 浅色主题")
        self.theme_btn.clicked.connect(self._toggle_theme)
        lay.addWidget(self.theme_btn)
        return bar

    def _titlebar_press(self, event):
        if event.button() == Qt.LeftButton and self.windowHandle():
            self.windowHandle().startSystemMove()
            event.accept()

    def _titlebar_double(self, event):
        if event.button() == Qt.LeftButton:
            self._toggle_max()

    def _toggle_max(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if not self._first_show:
            self._first_show = True
            scr = QApplication.primaryScreen()
            if scr is not None:
                g = scr.availableGeometry()
                w = min(self.DEFAULT_W, int(g.width() * 0.9))
                h = min(self.DEFAULT_H, int(g.height() * 0.9))
                w = max(w, self.minimumWidth())
                h = max(h, self.minimumHeight())
                self.resize(w, h)
                self.move(g.x() + max(0, (g.width() - w) // 2),
                          g.y() + max(0, (g.height() - h) // 2))

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key_F11:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
            event.accept()
            return
        if event.key() == Qt.Key_Escape and self.isFullScreen():
            self.showNormal()
            event.accept()
            return
        super().keyPressEvent(event)

    def _edges_at(self, pos):
        m = self.RESIZE_MARGIN
        r = self.rect()
        edges = None
        for cond, e in ((pos.x() <= m, Qt.Edge.LeftEdge),
                        (pos.x() >= r.width() - m, Qt.Edge.RightEdge),
                        (pos.y() <= m, Qt.Edge.TopEdge),
                        (pos.y() >= r.height() - m, Qt.Edge.BottomEdge)):
            if cond:
                edges = e if edges is None else (edges | e)
        return edges

    def mouseMoveEvent(self, event):  # noqa: N802
        edges = self._edges_at(event.position().toPoint())
        cursor = Qt.ArrowCursor
        if edges is not None:
            left = bool(edges & Qt.Edge.LeftEdge)
            right = bool(edges & Qt.Edge.RightEdge)
            top = bool(edges & Qt.Edge.TopEdge)
            bottom = bool(edges & Qt.Edge.BottomEdge)
            if (left and top) or (right and bottom):
                cursor = Qt.SizeFDiagCursor
            elif (right and top) or (left and bottom):
                cursor = Qt.SizeBDiagCursor
            elif left or right:
                cursor = Qt.SizeHorCursor
            else:
                cursor = Qt.SizeVerCursor
        self.setCursor(QCursor(cursor))
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton and self.windowHandle():
            edges = self._edges_at(event.position().toPoint())
            if edges is not None:
                self.windowHandle().startSystemResize(edges)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        self.setCursor(QCursor(Qt.ArrowCursor))
        super().mouseReleaseEvent(event)

    def _toggle_theme(self):
        self._dark = not self._dark
        apply_theme(QApplication.instance(), DARK if self._dark else LIGHT)
        self.theme_btn.setText("☀️" if self._dark else "🌙")
        for sw in self._switches:
            sw.apply_theme(THEME)
        for cb in self._color_buttons:
            cb._sync()

    # ---- 侧边栏 ----
    def _build_sidebar(self):
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(206)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(16, 18, 16, 16)
        lay.setSpacing(4)

        brand = QLabel("静电势")
        brand.setObjectName("SidebarBrand")
        lay.addWidget(brand)
        sub = QLabel("LOCPOT + CHGCAR")
        sub.setObjectName("SidebarSub")
        lay.addWidget(sub)
        lay.addSpacing(16)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for text, idx in (("表面视图", 0), ("日志", 1), ("使用说明", 2)):
            btn = QPushButton(text)
            btn.setObjectName("NavItem")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, i=idx: self._switch_page(i))
            self.nav_group.addButton(btn)
            lay.addWidget(btn)
            if idx == 0:
                btn.setChecked(True)
        lay.addStretch(1)
        self.lbl_file = QLabel("尚未载入数据")
        self.lbl_file.setObjectName("SidebarSub")
        self.lbl_file.setWordWrap(True)
        lay.addWidget(self.lbl_file)
        return side

    def _switch_page(self, idx):
        self.pages.setCurrentIndex(idx)
        self.page_title.setText(["表面视图", "运行日志", "使用说明"][idx])

    # ---- 动作栏 ----
    def _build_actionbar(self):
        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(24, 16, 24, 8)
        lay.setSpacing(10)
        self.page_title = QLabel("表面视图")
        self.page_title.setObjectName("H1")
        lay.addWidget(self.page_title)
        lay.addStretch(1)

        self.btn_png = QPushButton("导出 PNG")
        self.btn_png.setObjectName("Primary")
        self.btn_png.setCursor(Qt.PointingHandCursor)
        self.btn_png.clicked.connect(self._export_png)
        lay.addWidget(self.btn_png)
        return bar

    # ---- 页脚 ----
    def _build_footer(self):
        foot = QWidget()
        lay = QVBoxLayout(foot)
        lay.setContentsMargins(24, 4, 24, 14)
        lay.setSpacing(6)
        self.footer_status = QLabel("就绪 · 正在准备数据")
        self.footer_status.setObjectName("Footer")
        lay.addWidget(self.footer_status)
        return foot

    def _make_scroll(self, margins=(0, 0, 0, 0), spacing=12, width=None):
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.viewport().setAutoFillBackground(False)
        if width:
            area.setFixedWidth(width)
        inner = QWidget()
        inner.setObjectName("PageInner")
        inner.setAttribute(Qt.WA_StyledBackground, True)
        area.setWidget(inner)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(*margins)
        lay.setSpacing(spacing)
        return area, lay

    # ==================================================================
    # 表面视图页
    # ==================================================================
    def _page_viewer(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(24, 4, 18, 14)
        v.setSpacing(10)

        # ---- 视角工具条 (位于交互视图上方) ----
        bar = QHBoxLayout()
        bar.setSpacing(6)
        lbl = QLabel("视角")
        lbl.setObjectName("FieldLabel")
        bar.addWidget(lbl)
        for key, label in VIEW_BUTTONS:
            btn = QPushButton(label)
            btn.setObjectName("Secondary")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumWidth(56)
            btn.clicked.connect(lambda _=False, k=key: self._set_view(k))
            bar.addWidget(btn)
        bar.addStretch(1)
        btn_reset = QPushButton("重置视角")
        btn_reset.setObjectName("Ghost")
        btn_reset.setCursor(Qt.PointingHandCursor)
        btn_reset.clicked.connect(lambda: self.viewer.reset_view())
        bar.addWidget(btn_reset)
        v.addLayout(bar)

        # ---- 3D 视图 + 右侧控制面板 ----
        body = QHBoxLayout()
        body.setSpacing(16)
        self.viewer = EspViewer()
        self.viewer.ready.connect(self._on_viewer_ready)
        self.viewer.colorsSampled.connect(self._on_colors_sampled)
        self.colorbar = ColorBar()
        body.addWidget(self.colorbar, 0)
        body.addWidget(self.viewer, 1)

        panel, lay = self._make_scroll(margins=(0, 0, 8, 0), spacing=12, width=372)
        self._build_data_card(lay)
        self._build_iso_card(lay)
        self._build_color_card(lay)
        self._build_display_card(lay)
        lay.addStretch(1)
        body.addWidget(panel)
        v.addLayout(body, 1)
        self._on_scheme_changed(0)
        return page

    # -- 数据卡片 --
    def _build_data_card(self, lay):
        card = Card("数据文件")
        lay.addWidget(card)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(field_label("数据目录", 60))
        self.edit_dir = QLineEdit()
        self.edit_dir.setPlaceholderText("含 LOCPOT 与 CHGCAR 的目录")
        self.edit_dir.textChanged.connect(self._refresh_detected)
        row.addWidget(self.edit_dir, 1)
        b1 = QPushButton("浏览")
        b1.setObjectName("Browse")
        b1.setCursor(Qt.PointingHandCursor)
        b1.clicked.connect(self._pick_dir)
        row.addWidget(b1)
        card.add_layout(row)

        self.lbl_detected = QLabel("")
        self.lbl_detected.setObjectName("Hint")
        self.lbl_detected.setWordWrap(True)
        card.add(self.lbl_detected)

        row4 = QHBoxLayout()
        row4.setSpacing(8)
        self.btn_load = QPushButton("重新载入")
        self.btn_load.setObjectName("Primary")
        self.btn_load.setCursor(Qt.PointingHandCursor)
        self.btn_load.clicked.connect(self._start_load)
        row4.addWidget(self.btn_load, 1)
        card.add_layout(row4)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        card.add(self.progress)
        card.add(hint_label("自动读取目录中的 LOCPOT 与 CHGCAR 两个文件。"))

    # -- 等值面卡片 --
    def _build_iso_card(self, lay):
        card = Card("等值面")
        lay.addWidget(card)
        self.row_dens_level = SliderRow("密度水平", 0.0002, 0.5, 0.01,
                                        decimals=4, log=True, step=0.005)
        self.row_dens_level.changed.connect(lambda _: self._debounce.start())
        card.add(self.row_dens_level)
        card.add(hint_label("电荷密度等值面水平 (e/Å³)。分子常用 ~0.01, 固体常用 ~0.1。"))

    # -- 颜色卡片 --
    def _build_color_card(self, lay):
        card = Card("颜色 / 彩条")
        lay.addWidget(card)

        r3 = QHBoxLayout()
        r3.addWidget(field_label("配色方案", 72))
        self.combo_scheme = ComboBox()
        for key, label in SCHEMES:
            self.combo_scheme.addItem(label, key)
        self.combo_scheme.currentIndexChanged.connect(self._on_scheme_changed)
        r3.addWidget(self.combo_scheme, 1)
        card.add_layout(r3)

        r4 = QHBoxLayout()
        r4.addWidget(field_label("色条范围", 72))
        self.spin_vmin = QDoubleSpinBox()
        self.spin_vmin.setRange(-100000, 100000)
        self.spin_vmin.setDecimals(2)
        self.spin_vmin.setValue(-12.0)
        self.spin_vmax = QDoubleSpinBox()
        self.spin_vmax.setRange(-100000, 100000)
        self.spin_vmax.setDecimals(2)
        self.spin_vmax.setValue(12.0)
        self.spin_vmin.valueChanged.connect(lambda _: self._apply(0))
        self.spin_vmax.valueChanged.connect(lambda _: self._apply(0))
        r4.addWidget(self.spin_vmin)
        lab = QLabel("~")
        lab.setObjectName("Hint")
        r4.addWidget(lab)
        r4.addWidget(self.spin_vmax)
        card.add_layout(r4)
        card.add(hint_label("彩条范围即电势映射区间 (eV), 可自定义; "
                            "3D 视图左侧彩条会同步更新。"))

        # 电势符号: VASP LOCPOT 是电子势能 (= -静电势), 默认取负得到标准 ESP
        rr = QHBoxLayout()
        rr.addWidget(field_label("标准 ESP", 72))
        rr.addStretch(1)
        self.sw_sign = Switch()
        self.sw_sign.setChecked(True, animate=False)
        self.sw_sign.apply_theme(THEME)
        self._switches.append(self.sw_sign)
        self.sw_sign.toggled.connect(lambda _: self._apply(0))
        rr.addWidget(self.sw_sign)
        card.add_layout(rr)
        card.add(hint_label("开 = 标准静电势 ESP (= −LOCPOT): 电负性大的 O 为负/蓝, "
                            "H 为正/红。关 = 原始 LOCPOT。"))

        self.color_triple = QWidget()
        v3 = QVBoxLayout(self.color_triple)
        v3.setContentsMargins(0, 0, 0, 0)
        v3.setSpacing(6)
        for name, default in (("低", "#FF0000"), ("中", "#00FF00"), ("高", "#0000FF")):
            rr = QHBoxLayout()
            rr.addWidget(field_label(f"{name}值颜色", 72))
            cb = ColorButton(default)
            self._color_buttons.append(cb)
            cb.colorChanged.connect(lambda _: self._apply(0))
            rr.addWidget(cb, 1)
            v3.addLayout(rr)
        card.add(self.color_triple)

    # -- 显示卡片 --
    def _build_display_card(self, lay):
        card = Card("显示选项")
        lay.addWidget(card)

        self.row_opacity = SliderRow("不透明度", 0.05, 1.0, 0.80,
                                     decimals=2, step=0.05)
        self.row_opacity.changed.connect(lambda _: self._debounce.start())
        card.add(self.row_opacity)

        r = QHBoxLayout()
        r.addWidget(field_label("原子样式", 72))
        self.combo_atom = ComboBox()
        for key, label in ATOM_STYLES:
            self.combo_atom.addItem(label, key)
        self.combo_atom.currentIndexChanged.connect(lambda _: self._apply(0))
        r.addWidget(self.combo_atom, 1)
        card.add_layout(r)

        self.row_atom_scale = SliderRow("原子半径", 0.2, 2.0, 1.0,
                                        decimals=2, step=0.05)
        self.row_atom_scale.changed.connect(lambda _: self._debounce.start())
        card.add(self.row_atom_scale)

        rr = QHBoxLayout()
        rr.addWidget(field_label("显示晶胞", 72))
        rr.addStretch(1)
        self.sw_cell = Switch()
        self.sw_cell.setChecked(True, animate=False)
        self.sw_cell.apply_theme(THEME)
        self._switches.append(self.sw_cell)
        self.sw_cell.toggled.connect(lambda _: self._apply(0))
        rr.addWidget(self.sw_cell)
        card.add_layout(rr)

        rc = QHBoxLayout()
        rc.addWidget(field_label("背景色", 72))
        self.btn_bg = ColorButton("#FFFFFF")
        self._color_buttons.append(self.btn_bg)
        self.btn_bg.colorChanged.connect(lambda _: self._apply(0))
        rc.addWidget(self.btn_bg, 1)
        card.add_layout(rc)

        rcell = QHBoxLayout()
        rcell.addWidget(field_label("晶胞颜色", 72))
        self.btn_cell = ColorButton("#666666")
        self._color_buttons.append(self.btn_cell)
        self.btn_cell.colorChanged.connect(lambda _: self._apply(0))
        rcell.addWidget(self.btn_cell, 1)
        card.add_layout(rcell)

    # ==================================================================
    # 其它页
    # ==================================================================
    def _page_log(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(24, 8, 24, 16)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlainText("静电势表面查看器已启动。")
        v.addWidget(self.log, 1)
        return page

    def _page_about(self):
        page = QWidget()
        area, lay = self._make_scroll(margins=(24, 8, 24, 16), spacing=14)
        h = QHBoxLayout(page)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(area, 1)

        card = Card("使用说明")
        lay.addWidget(card)
        text = QLabel(
            "1. 「数据目录」指向包含 VASP 结果文件的目录, 程序会自动读取\n"
            "   LOCPOT (电势) 与 CHGCAR (电荷密度) 两个文件。\n"
            "2. 视图为 VESTA 风格的「电荷密度等值面 + 静电势彩虹着色」:\n"
            "   · 「等值面 → 密度水平」调节等值面 (e/Å³, 常用 0.1);\n"
            "   · 「颜色 → 配色方案 / 色条范围」改变渐变与电势映射区间,\n"
            "     左侧彩条 colorbar 与之一致。\n"
            "3. 「视角」提供 6 个标准视角 (基于实际晶胞矢量, 正交投影);\n"
            "   也可拖动旋转、滚轮缩放。\n"
            "4. 右上角「导出 PNG」保存当前视图。")
        text.setWordWrap(True)
        text.setObjectName("FieldLabel")
        card.add(text)

        card2 = Card("关于")
        lay.addWidget(card2)
        t2 = QLabel("静电势表面查看器 · 基于 PySide6 + 本地 3Dmol.js (离线可用)。\n"
                    "GUI 设计参考 CONT-gif 项目。")
        t2.setWordWrap(True)
        t2.setObjectName("Hint")
        card2.add(t2)
        lay.addStretch(1)
        return page

    # ==================================================================
    # 数据目录 / 载入
    # ==================================================================
    def _pick_dir(self):
        start = self.edit_dir.text() or str(app_dir())
        path = QFileDialog.getExistingDirectory(self, "选择数据目录", start)
        if path:
            self.edit_dir.setText(path)

    def _detected(self) -> Tuple[Optional[Path], Optional[Path]]:
        d = Path(self.edit_dir.text().strip() or ".")
        pot = next((d / n for n in POT_NAMES if (d / n).is_file()), None)
        dens = next((d / n for n in DENS_NAMES if (d / n).is_file()), None)
        return pot, dens

    def _refresh_detected(self, *_):
        pot, dens = self._detected()
        p = pot.name if pot else "未找到"
        c = dens.name if dens else "未找到"
        self.lbl_detected.setText(f"检测到:  {p}  +  {c}")

    def _start_load(self):
        pot, dens = self._detected()
        if pot is None:
            QMessageBox.warning(
                self, "提示",
                "该目录下未找到 LOCPOT / POT 文件。\n请重新选择数据目录。")
            return
        if self.worker is not None and self.worker.isRunning():
            return

        self.btn_load.setEnabled(False)
        self.progress.setValue(0)
        self.footer_status.setText("正在读取文件 …")
        self._log(f"载入目录: {self.edit_dir.text()}\n  电势: {pot}\n  密度: {dens}")

        self.worker = LoadWorker(str(pot), str(dens) if dens else None)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._on_loaded)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_progress(self, done, total):
        if total > 0:
            self.progress.setValue(int(done / total * 100))

    def _on_failed(self, msg):
        self.btn_load.setEnabled(True)
        self.progress.setValue(0)
        self._log("载入失败:\n" + msg)
        self.footer_status.setText("载入失败, 详见日志页")
        QMessageBox.critical(self, "载入失败", msg.split("\n")[0])

    def _on_loaded(self, res: dict):
        self._ready = False
        self.btn_load.setEnabled(True)
        self.progress.setValue(100)
        self.data = res
        st: V.Structure = res["struct"]
        stats = res["stats"]
        nx, ny, nz = res["dims"]
        factor = res.get("factor", 1)
        self._log(f"结构: {st.n_atoms} 原子; 原始网格 {nx}×{ny}×{nz}; "
                  f"降采样 ×{factor}")

        if res["dens"] and "rho_p95" in stats:
            dflt = float(stats.get("dens_default", 0.01))
            self.row_dens_level.set_range(max(0.0001, dflt / 50.0), dflt * 50.0)
            self.row_dens_level.set_value(dflt)
            half = 1.1 * max(abs(stats["esp_lo"]), abs(stats["esp_hi"]))
            half = max(half, 0.5)
            self.spin_vmin.setValue(-half)
            self.spin_vmax.setValue(half)

        self.lbl_file.setText(f"{Path(self.edit_dir.text()).name}\n"
                              f"{st.n_atoms} 原子")
        # 分子（大真空盒）默认不显示晶胞框
        fit = V.fit_sphere(st)
        self.sw_cell.setChecked(bool(fit[4] > 0.5), animate=False)
        self._load_viewer(res, fit)

    def _load_viewer(self, res: dict, fit=None):
        st: V.Structure = res["struct"]
        cfg = self._collect_settings()
        self._ready = False
        if fit is None:
            fit = V.fit_sphere(st)
        self.viewer.load_scene(st.xyz(), st.cell_edges(), res["pot"],
                               res["dens"], V.view_quaternions(st.cell),
                               fit, cfg)
        self.footer_status.setText("正在初始化 3D 视图 (正交投影) …")

    def _on_viewer_ready(self):
        self._ready = True
        self._apply(0)
        self.footer_status.setText("就绪 · 正交投影 · 可拖动旋转 / 滚轮缩放")

    def _on_colors_sampled(self, colors):
        self.colorbar.set_colors(colors)

    # ==================================================================
    # 设置
    # ==================================================================
    def _on_scheme_changed(self, _):
        self.color_triple.setVisible(self.combo_scheme.currentData() == "custom")
        self._apply(0)

    def _set_view(self, key: str):
        self._view_name = key
        self.viewer.set_named_view(key)

    def _triple_colors(self):
        btns = self.color_triple.findChildren(ColorButton)
        return [b.color() for b in btns[:3]] if len(btns) >= 3 else \
               ["#FF0000", "#00FF00", "#0000FF"]

    def _collect_settings(self) -> Dict:
        return {
            "dens_level": self.row_dens_level.value(),
            "opacity": self.row_opacity.value(),
            "smoothness": 3,
            "scheme": self.combo_scheme.currentData() or "rainbow",
            "scheme_min": self.spin_vmin.value(),
            "scheme_max": self.spin_vmax.value(),
            "scheme_colors": self._triple_colors(),
            "pot_sign": -1 if self.sw_sign.isChecked() else 1,
            "atom_style": self.combo_atom.currentData() or "ballstick",
            "atom_scale": self.row_atom_scale.value(),
            "show_cell": self.sw_cell.isChecked(),
            "cell_color": self.btn_cell.color(),
            "bg": self.btn_bg.color(),
            "view_name": self._view_name,
        }

    def _apply(self, debounce_ms: int = 0):
        if self.data is None or not self._ready:
            return
        cfg = self._collect_settings()
        self.viewer.apply_settings(cfg, debounce_ms=debounce_ms)
        self.colorbar.set_range(cfg["scheme_min"], cfg["scheme_max"])
        self.footer_status.setText(
            f"密度水平 {cfg['dens_level']:.4f} e/Å³ · "
            f"电势范围 {cfg['scheme_min']:.2f} ~ {cfg['scheme_max']:.2f} eV")

    # ==================================================================
    # 导出
    # ==================================================================
    def _export_png(self):
        if self.data is None:
            QMessageBox.information(self, "提示", "请先载入数据。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 PNG", str(app_dir() / "ESP_surface.png"),
            "PNG 图片 (*.png)")
        if not path:
            return

        def done(raw):
            if not raw:
                QMessageBox.warning(self, "导出失败", "未能从 3D 视图获取图像。")
                return
            try:
                img = QImage.fromData(raw)
                if img.isNull():
                    raise ValueError("无法解析 3D 图像")
                bar = self.colorbar.grab().toImage()
                gap = 16
                W = img.width() + gap + bar.width()
                H = max(img.height(), bar.height())
                out = QImage(W, H, QImage.Format_ARGB32)
                out.fill(QColor(self.btn_bg.color()))
                painter = QPainter(out)
                painter.drawImage(0, 0, img)
                painter.drawImage(img.width() + gap,
                                  (H - bar.height()) // 2, bar)
                painter.end()
                if not out.save(path):
                    raise ValueError("保存失败")
                self._log(f"已导出 PNG: {path}")
                self.footer_status.setText(f"已导出: {path}")
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "导出失败", str(exc))

        self.viewer.grab_png(done)

    # ==================================================================
    def _log(self, text):
        if hasattr(self, "log"):
            self.log.appendPlainText(text)
        print(text)

    def closeEvent(self, event):  # noqa: N802
        try:
            self.viewer.shutdown()
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(event)


# ==========================================================================
def main():
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    selftest = "--selftest" in sys.argv
    argv = [a for a in sys.argv if a != "--selftest"]
    app = QApplication(argv)
    apply_theme(app, LIGHT)
    data_dir = argv[1] if len(argv) > 1 else None
    win = MainWindow(data_dir=data_dir)
    win.show()

    if selftest:
        import time
        t0 = time.time()

        def poll():
            if win.data is not None and win._ready:
                print("SELFTEST OK", flush=True)
                app.exit(0)
            elif time.time() - t0 > 90:
                print("SELFTEST FAIL", flush=True)
                app.exit(1)
            else:
                QTimer.singleShot(500, poll)

        QTimer.singleShot(500, poll)
        sys.exit(app.exec())

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
