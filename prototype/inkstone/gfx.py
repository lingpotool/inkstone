"""渲染后端。

这一层只负责一件事：把「画一个圆角矩形 / 写一行字」翻译成 pyglet 图元。
上层 UI 系统不知道 pyglet 的存在 —— 想换渲染后端，只改这个文件。
"""

import pyglet
from pyglet.shapes import Rectangle, RoundedRectangle, Line
from pyglet.text import Label

from .tokens import FONT_CANDIDATES, LIGHT


def _pick_font():
    for name in FONT_CANDIDATES:
        try:
            if pyglet.font.have_font(name):
                return name
        except Exception:
            continue
    return None


FONT = _pick_font()

_measure_cache = {}


def measure(text, size, bold=False, width=None):
    """返回 (宽, 高)。布局阶段用，不产生任何绘制对象。"""
    key = (text, size, bold, width)
    hit = _measure_cache.get(key)
    if hit is not None:
        return hit
    multiline = width is not None or "\n" in (text or "")
    lab = Label(
        text or "",
        font_name=FONT,
        font_size=size,
        weight="bold" if bold else "normal",
        width=width if width is not None else (100000 if multiline else None),
        multiline=multiline,
        anchor_x="left",
        anchor_y="top",
    )
    out = (lab.content_width, lab.content_height)
    lab.delete()
    if len(_measure_cache) > 4000:
        _measure_cache.clear()
    _measure_cache[key] = out
    return out


class Painter:
    """按帧复用图元对象：布局变了就更新属性，不重建。"""

    def __init__(self):
        self.batch = pyglet.graphics.Batch()
        self.vh = 0
        self.theme = LIGHT
        self.base_bg = LIGHT.bg
        self._shapes = {}
        self._labels = {}
        self._label_cfg = {}
        self._used_shapes = set()
        self._used_labels = set()
        self._text_order = []

    def begin(self, viewport_h):
        self.vh = viewport_h
        self._used_shapes = set()
        self._used_labels = set()
        self._text_order = []

    # ---------- 图元 ----------

    def _shape(self, key, cls, **kw):
        sh = self._shapes.get(key)
        if sh is None or type(sh) is not cls:
            if sh is not None:
                sh.delete()
            sh = cls(batch=self.batch, **kw)
            self._shapes[key] = sh
            return sh, True
        return sh, False

    def rrect(self, key, x, y, w, h, radius, color):
        if w <= 0 or h <= 0:
            return None
        radius = min(radius, w / 2, h / 2)
        if radius <= 0.5:  # pyglet 的圆角矩形不接受 0 半径，退化成矩形
            return self.rect(key, x, y, w, h, color)
        sh, new = self._shape(
            key, RoundedRectangle,
            x=0, y=0, width=1, height=1, radius=1, segments=8,
        )
        sh.x, sh.y = x, self.vh - y - h
        sh.width, sh.height = max(0.0, w), max(0.0, h)
        sh.radius = radius
        sh.color = tuple(color)
        sh.visible = True
        self._used_shapes.add(key)
        return sh

    def rect(self, key, x, y, w, h, color):
        sh, _ = self._shape(key, Rectangle, x=0, y=0, width=1, height=1)
        sh.x, sh.y = x, self.vh - y - h
        sh.width, sh.height = max(0.0, w), max(0.0, h)
        sh.color = tuple(color)
        sh.visible = True
        self._used_shapes.add(key)
        return sh

    def line(self, key, x1, y1, x2, y2, thickness, color):
        sh, _ = self._shape(key, Line, x=0, y=0, x2=1, y2=1, thickness=1)
        sh.x, sh.y = x1, self.vh - y1
        sh.x2, sh.y2 = x2, self.vh - y2
        sh.width = thickness
        sh.color = tuple(color)
        sh.visible = True
        self._used_shapes.add(key)
        return sh

    def box(self, key, x, y, w, h, radius=0, bg=None, border=None, border_w=1):
        """带描边的圆角盒：先画一圈边框色，再用背景色内缩盖住中间。"""
        if w <= 0 or h <= 0:
            return
        if border is not None:
            self.rrect((key, "bo"), x, y, w, h, radius, border)
            self.rrect(
                (key, "bi"), x + border_w, y + border_w,
                w - 2 * border_w, h - 2 * border_w,
                max(0, radius - border_w),
                bg if bg is not None else self.base_bg,
            )
        elif bg is not None:
            self.rrect((key, "bg"), x, y, w, h, radius, bg)

    # ---------- 文本 ----------

    def text(self, key, text, x, y, size, color, width=None, bold=False, align="left"):
        cfg = (size, tuple(color), width, bold, align)
        lab = self._labels.get(key)
        if lab is None or self._label_cfg.get(key) != cfg:
            if lab is not None:
                lab.delete()
            multiline = width is not None or "\n" in (text or "")
            lab = Label(
                text or "",
                font_name=FONT,
                font_size=size,
                weight="bold" if bold else "normal",
                color=tuple(color) + (255,),
                x=x, y=self.vh - y,
                width=width if width is not None else (100000 if multiline else None),
                multiline=multiline,
                anchor_x=align,
                anchor_y="top",
            )
            self._labels[key] = lab
            self._label_cfg[key] = cfg
        else:
            if lab.text != (text or ""):
                lab.text = text or ""
            lab.x, lab.y = x, self.vh - y
        self._used_labels.add(key)
        self._text_order.append(key)
        return lab

    # ---------- 收尾 ----------

    def end(self):
        for k in [k for k in self._shapes if k not in self._used_shapes]:
            self._shapes[k].delete()
            del self._shapes[k]
        for k in [k for k in self._labels if k not in self._used_labels]:
            self._labels[k].delete()
            del self._labels[k]
            self._label_cfg.pop(k, None)

    def flush(self):
        self.batch.draw()
        for k in self._text_order:
            lab = self._labels.get(k)
            if lab is not None:
                lab.draw()
