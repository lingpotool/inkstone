"""节点与布局引擎。

一套极简 flex：方向 direction、间距 gap、主轴分配 justify、交叉轴对齐 align、
尺寸 w/h 支持 固定整数 / 'fill'(吃掉剩余空间) / None(按内容)。
"""

from .tokens import LIGHT


def _color(value, theme):
    """支持用字符串写语义色：bg='surface' / border=('border', 1)。"""
    if isinstance(value, str):
        return getattr(theme, value, None)
    return value


def _pad(v):
    if v is None:
        return (0, 0, 0, 0)
    if isinstance(v, (int, float)):
        return (v, v, v, v)
    if len(v) == 2:
        return (v[0], v[1], v[0], v[1])
    return tuple(v)  # top, right, bottom, left


class Node:
    def __init__(self, children=None, **kw):
        self.w = kw.pop("w", None)
        self.h = kw.pop("h", None)
        self.min_w = kw.pop("min_w", 0)
        self.min_h = kw.pop("min_h", 0)
        self.padding = _pad(kw.pop("padding", 0))
        self.gap = kw.pop("gap", 0)
        self.direction = kw.pop("direction", "column")
        self.align = kw.pop("align", "start")      # 交叉轴
        self.justify = kw.pop("justify", "start")  # 主轴
        self.bg = kw.pop("bg", None)
        self.radius = kw.pop("radius", 0)
        self.border = kw.pop("border", None)       # (颜色, 宽度)
        self.on_click = kw.pop("on_click", None)
        self.id = kw.pop("id", None)
        self.grow = kw.pop("grow", False)          # 简写：沿主轴填充

        self.children = []
        self.parent = None
        self.box = (0, 0, 0, 0)     # 外框 x, y, w, h
        self.cbox = (0, 0, 0, 0)    # 内容框
        self.hover = False
        self.pressed = False
        self.focused = False
        self.interactive = False
        self.focusable = False
        self.cursor = None
        self.theme = LIGHT

        for c in children or []:
            self.add(c)

    # ---------- 树 ----------

    def add(self, *nodes):
        for n in nodes:
            if n is None:
                continue
            n.parent = self
            self.children.append(n)
        return self

    def find(self, node_id):
        if self.id == node_id:
            return self
        for c in self.children:
            got = c.find(node_id)
            if got:
                return got
        return None

    # ---------- 尺寸测量 ----------

    def content_intrinsic_w(self):
        return 0

    def content_intrinsic_h(self, w):
        return 0

    def intrinsic_w(self):
        if isinstance(self.w, (int, float)):
            return self.w
        inner = 0
        if self.children:
            if self.direction == "row":
                inner = sum(c.intrinsic_w() for c in self.children) + self.gap * (len(self.children) - 1)
            else:
                inner = max(c.intrinsic_w() for c in self.children)
        else:
            inner = self.content_intrinsic_w()
        return max(self.min_w, inner) + self.padding[1] + self.padding[3]

    def intrinsic_h(self, w=None):
        if isinstance(self.h, (int, float)):
            return self.h
        cw = None
        if w:
            cw = max(0, w - self.padding[1] - self.padding[3])
        inner = 0
        if self.children:
            if self.direction == "row":
                widths = self._main_sizes(cw, None, "row")
                inner = max(c.intrinsic_h(widths[i]) for i, c in enumerate(self.children))
            else:
                inner = sum(c.intrinsic_h(cw) for c in self.children) + self.gap * (len(self.children) - 1)
        else:
            inner = self.content_intrinsic_h(cw)
        return max(self.min_h, inner) + self.padding[0] + self.padding[2]

    def _main_sizes(self, avail, cross, direction):
        """返回主轴上每个孩子的尺寸；'fill'/grow 先记 0，稍后分配剩余空间。

        avail = 主轴可用长度；cross = 交叉轴可用长度（column 布局里测高需要用到宽度）。
        """
        kids = self.children
        sizes = []
        fixed = 0
        flex_n = 0
        for c in kids:
            if direction == "row":
                explicit = c.w
                if c.grow and explicit is None:
                    explicit = "fill"
                if isinstance(explicit, (int, float)):
                    sizes.append(explicit)
                    fixed += explicit
                elif explicit == "fill":
                    sizes.append(0)
                    flex_n += 1
                else:
                    s = c.intrinsic_w()
                    sizes.append(s)
                    fixed += s
            else:
                explicit = c.h
                if c.grow and explicit is None:
                    explicit = "fill"
                if isinstance(explicit, (int, float)):
                    sizes.append(explicit)
                    fixed += explicit
                elif explicit == "fill":
                    sizes.append(0)
                    flex_n += 1
                else:
                    s = c.intrinsic_h(cross)
                    sizes.append(s)
                    fixed += s
        gaps = self.gap * max(0, len(kids) - 1)
        if flex_n and avail:
            free = avail - fixed - gaps
            share = max(0.0, free) / flex_n
            for i, c in enumerate(kids):
                if sizes[i] == 0 and (
                    (direction == "row" and (c.w == "fill" or (c.grow and c.w is None)))
                    or (direction == "column" and (c.h == "fill" or (c.grow and c.h is None)))
                ):
                    sizes[i] = share
        return sizes

    @staticmethod
    def _offset(total, size, align):
        if align == "center":
            return (total - size) / 2
        if align == "end":
            return total - size
        return 0

    # ---------- 布局 ----------

    def layout(self, x, y, w, h):
        if isinstance(self.w, (int, float)):
            w = self.w
        if isinstance(self.h, (int, float)):
            h = self.h
        w = max(self.min_w, w)
        h = max(self.min_h, h)
        self.box = (x, y, w, h)
        cx = x + self.padding[3]
        cy = y + self.padding[0]
        cw = max(0, w - self.padding[3] - self.padding[1])
        ch = max(0, h - self.padding[0] - self.padding[2])
        self.cbox = (cx, cy, cw, ch)
        if not self.children:
            return

        if self.direction == "row":
            sizes = self._main_sizes(cw, ch, "row")
            total = sum(sizes) + self.gap * (len(sizes) - 1)
            pos = cx + self._offset(cw, total, self.justify)
            step = self.gap
            if self.justify == "space_between" and len(sizes) > 1 and cw > total:
                step = self.gap + (cw - total) / (len(sizes) - 1)
            for i, c in enumerate(self.children):
                cw_i = sizes[i]
                ch_i = self._cross(c, ch, cw_i)
                cy_i = cy + self._offset(ch, ch_i, self.align)
                c.layout(pos, cy_i, cw_i, ch_i)
                pos += cw_i + step
        else:
            sizes = self._main_sizes(ch, cw, "column")
            total = sum(sizes) + self.gap * (len(sizes) - 1)
            pos = cy + self._offset(ch, total, self.justify)
            step = self.gap
            if self.justify == "space_between" and len(sizes) > 1 and ch > total:
                step = self.gap + (ch - total) / (len(sizes) - 1)
            for i, c in enumerate(self.children):
                ch_i = sizes[i]
                cw_i = self._cross(c, cw)
                cx_i = cx + self._offset(cw, cw_i, self.align)
                c.layout(cx_i, pos, cw_i, ch_i)
                pos += ch_i + step

    def _cross(self, child, cross_size, main_size=None):
        if self.direction == "row":
            if isinstance(child.h, (int, float)):
                return child.h
            if child.h == "fill" or (child.grow and child.h is None):
                return cross_size
            return child.intrinsic_h(main_size)
        else:
            if isinstance(child.w, (int, float)):
                return child.w
            if child.w == "fill" or (child.grow and child.w is None):
                return cross_size
            return child.intrinsic_w()

    # ---------- 绘制 ----------

    def resolve_bg(self):
        return _color(self.bg, self.theme)

    def paint(self, p):
        x, y, w, h = self.box
        bw = 1
        border = None
        if self.border:
            border = _color(self.border[0], p.theme)
            bw = self.border[1] if len(self.border) > 1 else 1
        p.box(
            (id(self), "box"), x, y, w, h,
            radius=self.radius, bg=self.resolve_bg(),
            border=border, border_w=bw,
        )
        self.paint_content(p)
        for c in self.children:
            c.paint(p)

    def paint_content(self, p):
        pass

    # ---------- 事件 ----------

    def hit(self, px, py):
        x, y, w, h = self.box
        return x <= px <= x + w and y <= py <= y + h

    def on_press(self, px, py):
        return True

    def on_release(self, px, py):
        pass

    def on_enter(self):
        self.hover = True

    def on_leave(self):
        self.hover = False
        self.pressed = False

    def on_text(self, s):
        return False

    def on_key(self, symbol, modifiers):
        return False

    def on_focus(self):
        self.focused = True

    def on_blur(self):
        self.focused = False

    def collect(self, out):
        out.append(self)
        for c in self.children:
            c.collect(out)
