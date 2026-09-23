#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
静电势 3D 查看器 (QWebEngineView + 本地 3Dmol.js)。

只保留与参考图 (VESTA) 一致的一种视图:
    电荷密度等值面 + 静电势彩虹着色 (density)

特性:
    * 正交投影 (orthographic), 远近同大, 转动时大小恒定
    * 六个标准视角 (基于实际晶胞矢量): 正视/后视/俯视/仰视/左视/右视
    * 左侧彩条 (colorbar), 区间由设置中的电势范围 vmin~vmax 决定
    * 滚轮缩放 (正交相机距离驱动)

注意: 承载 QWebEngineView 的 Qt 祖先控件不能使用 QGraphicsDropShadowEffect,
      否则 WebGL 画布无法渲染 (阴影在 MainWindow 里手绘)。
"""

from __future__ import annotations

import base64
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)


def resource_dir() -> Path:
    """打包后资源 (3dmol/assets) 所在目录; 开发时即项目目录。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


BASE_DIR = Path(__file__).resolve().parent


def _read_3dmol_js() -> str:
    p = resource_dir() / "3dmol" / "3Dmol-min.js"
    if not p.is_file():
        p = BASE_DIR / "3dmol" / "3Dmol-min.js"
    if not p.is_file():
        raise FileNotFoundError(f"未找到本地 3Dmol.js: {p}")
    return p.read_text(encoding="utf-8")


# ==========================================================================
# HTML 模板
# ==========================================================================
_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>ESP</title>
<style>
html,body{{width:100%;height:100%;margin:0;padding:0;overflow:hidden;
          background:{bg};}}
#v{{position:absolute;left:0;top:0;width:100%;height:100%;}}
</style>
<script>{js}</script></head>
<body>
<div id="v"></div>
<script>
const XYZ   = {xyz};
const EDGES = {edges};
const POT   = {pot};
const DENS  = {dens};
const VIEWS = {views};
const FIT   = {fit};
const INIT  = {init};

function b64f32(b) {{
  var bin = atob(b), n = bin.length, u = new Uint8Array(n);
  for (var i = 0; i < n; i++) u[i] = bin.charCodeAt(i);
  return new Float32Array(u.buffer);
}}
function makeVD(v) {{
  if (!v) return null;
  var vd = new $3Dmol.VolumeData(v.header, 'cube');
  vd.data = b64f32(v.b64);
  return vd;
}}

var el = document.getElementById('v');
var viewer = $3Dmol.createViewer(el, {{backgroundColor: INIT.bg}});
viewer.addModel(XYZ, 'xyz');
var MODEL = viewer.getModel();

var potVD  = makeVD(POT);
var densVD = makeVD(DENS);
var cellShapes = [];
var LAST_CFG = INIT;
// 显示的电势符号: -1 = 标准 ESP (=-LOCPOT), +1 = 原始 LOCPOT
var POT_SIGN = 1;

// 取景用的点 (相对包围球中心), 用于逐视角贴合
var INC_CELL = (FIT.length > 4) ? FIT[4] > 0.5 : true;
var PTS = [];
(function () {{
  var a = MODEL.selectedAtoms({{}});
  for (var i = 0; i < a.length; i++)
    PTS.push([a[i].x - FIT[0], a[i].y - FIT[1], a[i].z - FIT[2]]);
  if (INC_CELL) {{
    for (var j = 0; j < EDGES.length; j++) {{
      var e = EDGES[j];
      PTS.push([e[0] - FIT[0], e[1] - FIT[1], e[2] - FIT[2]]);
      PTS.push([e[3] - FIT[0], e[4] - FIT[1], e[5] - FIT[2]]);
    }}
  }}
}})();

// ---------------------------------------------------------------- 配色
function scheme(cfg) {{
  var lo = cfg.scheme_min, hi = cfg.scheme_max;
  var RAINBOW = ['#0000FF', '#00FFFF', '#00FF00', '#FFFF00', '#FF8000', '#FF0000'];
  if (cfg.scheme === 'rainbow')
    return new $3Dmol.Gradient.CustomLinear(lo, hi, RAINBOW);
  if (cfg.scheme === 'rainbow_r')
    return new $3Dmol.Gradient.CustomLinear(lo, hi, RAINBOW.slice().reverse());
  if (cfg.scheme === 'roygb')   return new $3Dmol.Gradient.ROYGB(lo, hi);
  if (cfg.scheme === 'sinebow') return new $3Dmol.Gradient.Sinebow(lo, hi);
  if (cfg.scheme === 'bwr')
    return new $3Dmol.Gradient.CustomLinear(lo, hi, ['#1E6BFF', '#FFFFFF', '#FF3B30']);
  if (cfg.scheme === 'rwb')
    return new $3Dmol.Gradient.CustomLinear(lo, hi, ['#FF3B30', '#FFFFFF', '#1E6BFF']);
  return new $3Dmol.Gradient.CustomLinear(
      lo, hi, [cfg.scheme_colors[0], cfg.scheme_colors[1], cfg.scheme_colors[2]]);
}}

// 用配色方案采样颜色 (供 Qt 彩条使用, 保证与表面颜色一致)
window.sampleColors = function (n) {{
  var cfg = LAST_CFG || INIT;
  var lo = cfg.scheme_min, hi = cfg.scheme_max;
  if (hi === lo) hi = lo + 1e-6;
  var sch = scheme(cfg), out = [];
  for (var i = 0; i <= n; i++) {{
    var v = lo + (hi - lo) * i / n;
    var h = (sch.valueToHex(v) >>> 0).toString(16);
    while (h.length < 6) h = '0' + h;
    out.push('#' + h);
  }}
  return out;
}};

// ---------------------------------------------------------------- 几何
function drawCell(show, color) {{
  if (!show) return;
  for (var i = 0; i < EDGES.length; i++) {{
    var e = EDGES[i];
    cellShapes.push(viewer.addLine({{
      start: {{x: e[0], y: e[1], z: e[2]}},
      end:   {{x: e[3], y: e[4], z: e[5]}},
      color: color, dashed: false, linewidth: 1
    }}));
  }}
}}

function atomStyle(cfg) {{
  var s = cfg.atom_scale;
  if (cfg.atom_style === 'none') {{
    viewer.setStyle({{}}, {{sphere: {{hidden: true}}, stick: {{hidden: true}},
                           line: {{hidden: true}}, cross: {{hidden: true}}}});
  }} else if (cfg.atom_style === 'sphere') {{
    viewer.setStyle({{}}, {{sphere: {{radius: 0.9 * s}}}});
  }} else if (cfg.atom_style === 'stick') {{
    viewer.setStyle({{}}, {{stick: {{radius: 0.20 * s}}}});
  }} else if (cfg.atom_style === 'line') {{
    viewer.setStyle({{}}, {{line: {{linewidth: 2}}}});
  }} else {{
    viewer.setStyle({{}}, {{stick: {{radius: 0.15 * s}}, sphere: {{radius: 0.35 * s}}}});
  }}
}}

function clearAll() {{
  viewer.removeAllSurfaces();
  viewer.removeAllShapes();
  cellShapes = [];
}}

// 唯一的显示模式: 密度等值面 + 电势彩虹着色
window.applySettings = function(cfg) {{
  LAST_CFG = cfg;
  // 符号切换: VASP LOCPOT 是电子势能 (= -静电势), 默认取负得到标准 ESP
  var want = (cfg.pot_sign === undefined) ? 1 : cfg.pot_sign;
  if (potVD && want !== POT_SIGN) {{
    var arr = potVD.data;
    for (var i = 0; i < arr.length; i++) arr[i] = -arr[i];
    POT_SIGN = want;
  }}
  clearAll();
  atomStyle(cfg);
  drawCell(cfg.show_cell, cfg.cell_color);
  viewer.setBackgroundColor(cfg.bg, 1.0);
  if (densVD && potVD) {{
    var sh = viewer.addIsosurface(densVD, {{isoval: cfg.dens_level,
                                            opacity: cfg.opacity,
                                            smoothness: cfg.smoothness}});
    try {{ sh.updateStyle({{voldata: potVD, volscheme: scheme(cfg)}}); }}
    catch (err) {{ console.log('colorize failed: ' + err); }}
  }}
  applyCamera(null);
  viewer.render();
}};

// ================================================================ 相机
// 正交投影: 3Dmol 的 show() 会按相机距离重算正交视锥, 因此用距离驱动缩放。
viewer.setProjection('orthographic');

var ORTHO_ZOOM = 1.0;

function fovRadians() {{
  return Math.PI / 180.0 * (viewer.fov || viewer.camera.fov || 20.0);
}}
function viewAspect() {{
  var a = viewer.ASPECT || (viewer.WIDTH && viewer.HEIGHT
                            ? viewer.WIDTH / viewer.HEIGHT : 1.0);
  if (!isFinite(a) || a <= 0) a = 1.0;
  return a;
}}

function quatMatrix(q) {{
  var x = q[0], y = q[1], z = q[2], w = q[3];
  var x2 = x + x, y2 = y + y, z2 = z + z;
  var xx = x * x2, xy = x * y2, xz = x * z2;
  var yy = y * y2, yz = y * z2, zz = z * z2;
  var wx = w * x2, wy = w * y2, wz = w * z2;
  return [1 - (yy + zz), xy - wz, xz + wy,
          xy + wz, 1 - (xx + zz), yz - wx,
          xz - wy, yz + wx, 1 - (xx + yy)];
}}

function applyCamera(quat) {{
  if (quat) viewer.rotationGroup.quaternion.set(quat[0], quat[1], quat[2], quat[3]);
  var q = viewer.rotationGroup.quaternion;
  var M = quatMatrix([q.x, q.y, q.z, q.w]);
  var mx = 0, my = 0;
  for (var i = 0; i < PTS.length; i++) {{
    var p = PTS[i];
    var sx = M[0] * p[0] + M[1] * p[1] + M[2] * p[2];
    var sy = M[3] * p[0] + M[4] * p[1] + M[5] * p[2];
    if (Math.abs(sx) > mx) mx = Math.abs(sx);
    if (Math.abs(sy) > my) my = Math.abs(sy);
  }}
  var fov = fovRadians();
  var aspect = viewAspect();
  // 逐视角贴合: 正交视锥需要覆盖投影后的 bbox
  // 分子(不含晶胞盒)时预留密度等值面向外的延伸 (~3 Å)
  var extra = INC_CELL ? 0.0 : 3.0;
  var halfH = Math.max(my, mx / aspect) * 1.06 + extra;
  var dist = Math.max(halfH, 1e-3) / (ORTHO_ZOOM * Math.tan(fov));
  viewer.rotationGroup.position.set(0, 0, 0);
  viewer.rotationGroup.position.z = viewer.CAMERA_Z - dist;
  viewer.modelGroup.position.set(-FIT[0], -FIT[1], -FIT[2]);
  viewer.slabNear = -(FIT[3] + 40.0);
  viewer.slabFar = FIT[3] + 40.0;
  viewer.show();
}}

// 滚轮缩放 (正交)
(function () {{
  var e = document.getElementById('v');
  e.addEventListener('wheel', function (ev) {{
    ev.preventDefault(); ev.stopPropagation();
    var d = ev.deltaY;
    if (!d) return;
    ORTHO_ZOOM = Math.max(0.1, Math.min(20.0,
                          ORTHO_ZOOM * (d < 0 ? 1.1 : 1.0 / 1.1)));
    applyCamera(null);
  }}, {{passive: false, capture: true}});
}})();

window.setNamedView = function (name) {{
  var q = VIEWS[name] || VIEWS.top || [0, 0, 0, 1];
  applyCamera(q);
  viewer.render();
}};
window.resetView = function () {{
  ORTHO_ZOOM = 1.0;
  window.setNamedView(INIT.view_name || 'top');
}};
window.setBackground = function (c) {{
  viewer.setBackgroundColor(c, 1.0); viewer.render();
}};
window.pngURI = function () {{ return viewer.pngURI(); }};
window.surfaceCount = function () {{
  return {{shapes: viewer.shapes.length, surfaces: Object.keys(viewer.surfaces).length}};
}};

window.applySettings(INIT);
window.setNamedView(INIT.view_name || 'top');
window.ready = true;
</script>
</body></html>
"""


def build_html(xyz: str, edges, pot: Dict, dens: Optional[Dict],
               views: Dict, fit: List[float], settings: Dict) -> str:
    """生成完整的查看器 HTML (便于测试 / 直接复用)。"""
    dens_json = json.dumps(dens) if dens else "null"
    return _HTML.format(
        js=_read_3dmol_js(),
        bg=settings.get("bg", "#FFFFFF"),
        xyz=json.dumps(xyz),
        edges=json.dumps(edges),
        pot=json.dumps(pot),
        dens=dens_json,
        views=json.dumps(views),
        fit=json.dumps([float(v) for v in fit]),
        init=json.dumps(settings),
    )


class EspViewer(QWidget):
    """内嵌 3Dmol 的静电势查看器 (单一密度着色模式 + 正交投影)。"""

    ready = Signal()
    errorOccurred = Signal(str)
    colorsSampled = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tmpdir = Path(tempfile.mkdtemp(prefix="esp-viewer-"))
        self._ready = False
        self._pending: List[str] = []
        self._poll = 0
        self._settings: Dict = {}
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(360, 300)

        from PySide6.QtWebEngineWidgets import QWebEngineView
        self.web = QWebEngineView(self)
        self.web.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.web.loadFinished.connect(self._on_load)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        holder = QFrame()
        holder.setObjectName("Card")
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(6, 6, 6, 6)
        hl.addWidget(self.web)
        root.addWidget(holder, 1)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.lbl_tip = QLabel("拖动旋转 · 滚轮缩放 (正交投影)")
        self.lbl_tip.setObjectName("Chip")
        bar.addWidget(self.lbl_tip)
        bar.addStretch(1)
        root.addLayout(bar)

    # ------------------------------------------------------------------
    def load_scene(self, xyz: str, edges, pot: Dict, dens: Optional[Dict],
                   views: Dict, fit: List[float], settings: Dict):
        html = build_html(xyz, edges, pot, dens, views, fit, settings)
        self._settings = dict(settings)
        path = self._tmpdir / "esp.html"
        path.write_text(html, encoding="utf-8")
        self._ready = False
        self._pending = []
        self.web.load(QUrl.fromLocalFile(str(path.resolve())))

    # ------------------------------------------------------------------
    def _on_load(self, ok: bool):
        if not ok:
            self.errorOccurred.emit("页面加载失败")
            return
        self._poll = 0
        QTimer.singleShot(120, self._poll_ready)

    def _poll_ready(self):
        if self._ready:
            return
        self._poll += 1

        def cb(val):
            if val:
                self._ready = True
                for js in self._pending:
                    self.web.page().runJavaScript(js)
                self._pending.clear()
                self._sample_colors()
                self.ready.emit()
            elif self._poll < 150:
                QTimer.singleShot(120, self._poll_ready)

        self.web.page().runJavaScript("window.ready === true", cb)

    # ------------------------------------------------------------------
    def run(self, js: str, callback=None):
        if self._ready:
            if callback is not None:
                self.web.page().runJavaScript(js, callback)
            else:
                self.web.page().runJavaScript(js)
        else:
            self._pending.append(js)

    def apply_settings(self, settings: Dict, debounce_ms: int = 0):
        self._settings = dict(settings)
        js = f"window.applySettings({json.dumps(settings)});"
        if debounce_ms > 0:
            QTimer.singleShot(debounce_ms, lambda: self._push(js))
        else:
            self._push(js)

    def _push(self, js: str):
        self.run(js)
        self._sample_colors()

    def _sample_colors(self):
        """向页面采样当前配色方案的颜色, 供 Qt 彩条绘制。"""
        if not self._ready:
            return

        def cb(vals):
            if isinstance(vals, (list, tuple)) and len(vals) >= 2:
                self.colorsSampled.emit([str(v) for v in vals])

        self.run("window.sampleColors ? window.sampleColors(48) : [];", cb)

    def set_named_view(self, name: str):
        self.run(f"window.setNamedView({json.dumps(name)});")

    def reset_view(self):
        self.run("window.resetView();")

    def set_background(self, color: str):
        self.run(f"window.setBackground({json.dumps(color)});")

    def grab_png(self, callback):
        """取回 3Dmol 画布的 PNG dataURI。"""
        def cb(uri):
            try:
                if not uri or not uri.startswith("data:image/png;base64,"):
                    callback(None)
                    return
                callback(base64.b64decode(uri.split(",", 1)[1]))
            except Exception:  # noqa: BLE001
                callback(None)

        self.run("window.pngURI();", cb)

    # ------------------------------------------------------------------
    def shutdown(self):
        try:
            self.web.stop()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def closeEvent(self, event):  # noqa: N802
        self.shutdown()
        super().closeEvent(event)
