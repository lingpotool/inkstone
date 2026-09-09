"""内置控件。

全部自绘：没有系统控件、没有主题引擎、没有 CSS。
每个控件只做两件事 —— 报告自己想要多大 (intrinsic)，以及把自己画出来 (paint)。
"""

import time

from pyglet.window import key

from .gfx import measure
from .node import Node, _color
from .tokens import F, R, S


class Box(Node):
    pass


class Row(Node):
    def __init__(self, children=None, **kw):
        kw["direction"] = "row"
        kw.setdefault("align", "center")
        super().__init__(children, **kw)


class Column(Node):
    def __init__(self, children=None, **kw):
        kw["direction"] = "column"
        super().__init__(children, **kw)


class Stack(Node):
    """层叠容器：孩子按顺序画在同一个位置。"""

    def __init__(self, children=None, **kw):
        kw["direction"] = "stack"
        super().__init__(children, **kw)


class Spacer(Node):
    def __init__(self, size=8, horizontal=False, grow=False, **kw):
        if horizontal:
            kw["w"] = size
        else:
            kw["h"] = size
        kw["grow"] = grow
        super().__init__(**kw)


class Divider(Node):
    def __init__(self, **kw):
        kw.setdefault("h", 1)
        kw.setdefault("w", "fill")
        kw.setdefault("bg", "border")
        super().__init__(**kw)


class Text(Node):
    def __init__(self, value="", size=F.md, color=None, bold=False, wrap=True,
                 text_align="left", **kw):
        super().__init__(**kw)
        self.value = value
        self.size = size
        self.color = color
        self.bold = bold
        self.wrap = wrap
        self.text_align = text_align

    def content_intrinsic_w(self):
        return measure(self.value, self.size, self.bold, None)[0]

    def _wrap_width(self, w):
        """只有在真的放不下时才开多行，避免“刚好放得下”被舍进多行。"""
        if not (self.wrap and w):
            return None
        single = measure(self.value, self.size, self.bold, None)[0]
        return w if single > w + 0.5 else None

    def content_intrinsic_h(self, w):
        return measure(self.value, self.size, self.bold, self._wrap_width(w))[1]

    def paint_content(self, p):
        x, y, w, h = self.cbox
        width = self._wrap_width(w)
        tw, th = measure(self.value, self.size, self.bold, width)
        color = _color(self.color, p.theme) if self.color else p.theme.text
        if isinstance(self.h, (int, float)):
            y += (h - th) / 2
        if self.text_align == "center":
            x += w / 2
        elif self.text_align == "right":
            x += w
        p.text((id(self), "t"), self.value, x, y, self.size, color,
               width=width, bold=self.bold, align=self.text_align)


class Button(Node):
    def __init__(self, label="", on_click=None, variant="primary", size=F.md, **kw):
        kw.setdefault("padding", (0, 16))
        kw.setdefault("h", 36)
        kw.setdefault("radius", R.md)
        super().__init__(**kw)
        self.label = label
        self.variant = variant
        self.size = size
        self.on_click = on_click
        self.interactive = True
        self.cursor = "hand"

    def content_intrinsic_w(self):
        return measure(self.label, self.size, False, None)[0]

    def content_intrinsic_h(self, w):
        return measure(self.label, self.size, False, None)[1]

    def _colors(self, theme):
        v = self.variant
        if v == "ghost":
            return (None, theme.surface_alt if (self.hover or self.pressed) else None, theme.text)
        if v == "outline":
            return (theme.border_strong, theme.surface_alt if self.hover else theme.surface, theme.primary)
        if v == "danger":
            return (theme.danger, theme.danger if self.pressed else None, (255, 255, 255))
        base = theme.primary_press if self.pressed else (theme.primary_hover if self.hover else theme.primary)
        return (theme.primary, base, theme.on_primary)

    def resolve_bg(self):
        return self._colors(self.theme)[1]

    def paint(self, p):
        x, y, w, h = self.box
        bd, bg, fg = self._colors(p.theme)
        p.box((id(self), "box"), x, y, w, h, radius=self.radius, bg=bg, border=bd)
        tx, ty, tw, th = self.cbox
        mw, mh = measure(self.label, self.size, False, None)
        p.text((id(self), "t"), self.label, tx + tw / 2, ty + (th - mh) / 2,
               self.size, fg, align="center")

    def on_press(self, px, py):
        self.pressed = True
        return True

    def on_release(self, px, py):
        if self.pressed and self.on_click:
            self.on_click()
        self.pressed = False


class Input(Node):
    def __init__(self, value="", placeholder="", on_change=None, on_submit=None, **kw):
        kw.setdefault("padding", (0, 12))
        kw.setdefault("h", 38)
        kw.setdefault("radius", R.md)
        kw.setdefault("bg", "surface")
        kw.setdefault("w", 320)
        kw.setdefault("border", ("border_strong", 1))
        super().__init__(**kw)
        self.value = value
        self.placeholder = placeholder
        self.on_change = on_change
        self.on_submit = on_submit
        self.interactive = True
        self.focusable = True
        self.cursor = "text"
        self._caret_at = 0.0

    def content_intrinsic_h(self, w):
        return measure("字", F.md, False, None)[1]

    def paint(self, p):
        x, y, w, h = self.box
        bd = _color(self.border[0], p.theme) if self.border else None
        bd = p.theme.primary if self.focused else bd
        p.box((id(self), "box"), x, y, w, h, radius=self.radius,
              bg=_color(self.bg, p.theme), border=bd)
        cx, cy, cw, ch = self.cbox
        shown = self.value
        color = p.theme.text
        if not shown:
            shown = self.placeholder
            color = p.theme.text_faint
        _, th = measure(shown or "字", F.md, False, None)
        p.text((id(self), "t"), shown, cx, cy + (ch - th) / 2, F.md, color)
        if self.focused and int(time.time() * 2) % 2 == 0:
            tw, _ = measure(self.value, F.md, False, None)
            p.line((id(self), "caret"), cx + tw + 1, cy + 8, cx + tw + 1, cy + ch - 8,
                   1.5, p.theme.text)

    def on_press(self, px, py):
        return True

    def on_text(self, s):
        if s in ("\r", "\n", "\t"):
            return True
        self.value += s
        if self.on_change:
            self.on_change(self.value)
        return True

    def on_key(self, symbol, modifiers):
        if symbol == key.BACKSPACE:
            self.value = self.value[:-1]
            if self.on_change:
                self.on_change(self.value)
            return True
        if symbol in (key.ENTER, key.RETURN):
            if self.on_submit:
                self.on_submit(self.value)
                return True
        return False


class Card(Node):
    def __init__(self, children=None, **kw):
        kw.setdefault("bg", "surface")
        kw.setdefault("radius", R.lg)
        kw.setdefault("border", ("border", 1))
        kw.setdefault("padding", S.s4)
        super().__init__(children, **kw)


class Badge(Node):
    def __init__(self, text="", tone="primary", **kw):
        kw.setdefault("padding", (3, 10))
        kw.setdefault("radius", R.pill)
        super().__init__(**kw)
        self.text = text
        self.tone = tone
        self.size = F.sm

    def content_intrinsic_w(self):
        return measure(self.text, self.size, False, None)[0]

    def content_intrinsic_h(self, w):
        return measure(self.text, self.size, False, None)[1]

    def resolve_bg(self):
        t = self.theme
        return t.primary_soft if self.tone == "primary" else t.surface_alt

    def paint_content(self, p):
        x, y, w, h = self.cbox
        _, th = measure(self.text, self.size, False, None)
        fg = p.theme.primary if self.tone == "primary" else p.theme.text_dim
        p.text((id(self), "t"), self.text, x + w / 2, y + (h - th) / 2,
               self.size, fg, align="center")


class NavItem(Node):
    def __init__(self, label="", active=False, on_click=None, **kw):
        kw.setdefault("padding", (0, 12))
        kw.setdefault("h", 38)
        kw.setdefault("radius", R.md)
        kw.setdefault("w", "fill")
        super().__init__(**kw)
        self.label = label
        self.active = active
        self.on_click = on_click
        self.size = F.md
        self.interactive = True
        self.cursor = "hand"

    def content_intrinsic_h(self, w):
        return measure(self.label, self.size, False, None)[1]

    def resolve_bg(self):
        t = self.theme
        if self.active:
            return t.primary_soft
        return t.surface_alt if (self.hover or self.pressed) else None

    def paint_content(self, p):
        x, y, w, h = self.cbox
        _, th = measure(self.label, self.size, False, None)
        fg = p.theme.primary if self.active else p.theme.text
        p.text((id(self), "t"), self.label, x, y + (h - th) / 2, self.size, fg, bold=self.active)

    def on_press(self, px, py):
        self.pressed = True
        return True

    def on_release(self, px, py):
        if self.pressed and self.on_click:
            self.on_click()
        self.pressed = False


def Sidebar(items, active=0, on_select=None, title="", width=210):
    kids = []
    if title:
        kids.append(Text(title, size=F.sm, color="text_faint",
                         padding=(S.s1, S.s3, S.s2, S.s3)))
    for i, label in enumerate(items):
        kids.append(NavItem(label, active=(i == active),
                            on_click=(lambda idx: (lambda: on_select(idx)))(i) if on_select else None))
    kids.append(Spacer(grow=True))
    return Column(kids, w=width, bg="surface", padding=(S.s4, S.s3), gap=S.s1)
