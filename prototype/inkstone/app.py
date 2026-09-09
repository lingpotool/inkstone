"""应用外壳：窗口 + 事件路由 + 焦点管理。

每帧重算布局并重绘（保留模式，但没有 diff —— UI 规模小的时候这比脏矩形简单得多）。
"""

import pyglet
from pyglet import gl
from pyglet.window import key as pkey, mouse

from .gfx import Painter
from .tokens import THEMES

for _opt in ("win32_dpi_scaling", "dpi_scaling"):
    try:
        pyglet.options[_opt]  # 不存在会抛 KeyError
        pyglet.options[_opt] = True
    except Exception:
        pass

CURSOR_MAP = {"hand": "CURSOR_HAND", "text": "CURSOR_TEXT"}


class App:
    def __init__(self, title="inkstone", size=(1040, 680), theme="light", resizable=True):
        self.theme = THEMES[theme]
        self.root = None
        self.window = pyglet.window.Window(
            int(size[0]), int(size[1]), caption=title, resizable=resizable
        )
        self.painter = Painter()
        self.painter.theme = self.theme
        self.painter.base_bg = self.theme.bg
        self._hits = []
        self._pressed = None
        self._hover = None
        self.focus = None
        self._shot = None
        self.window.push_handlers(self)

    # ---------- 挂载 ----------

    def mount(self, root):
        self.root = root
        self._apply_theme(root)
        return self

    def _apply_theme(self, node):
        node.theme = self.theme
        for c in node.children:
            self._apply_theme(c)

    def set_theme(self, name):
        self.theme = THEMES[name]
        self.painter.theme = self.theme
        self.painter.base_bg = self.theme.bg
        if self.root:
            self._apply_theme(self.root)

    def refresh(self):
        if self.root:
            self._apply_theme(self.root)

    # ---------- 绘制 ----------

    def on_draw(self):
        bg = self.theme.bg
        gl.glClearColor(bg[0] / 255, bg[1] / 255, bg[2] / 255, 1.0)
        self.window.clear()
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        vw, vh = self.window.width, self.window.height
        p = self.painter
        p.begin(vh)
        if self.root:
            self.root.theme = self.theme
            self.root.layout(0, 0, vw, vh)
            self.root.paint(p)
            self._hits = []
            self.root.collect(self._hits)
        p.end()
        p.flush()
        if self._shot:
            try:
                pyglet.image.get_buffer_manager().get_color_buffer().save(self._shot)
            except Exception as exc:
                print("screenshot failed:", exc)
            self._shot = None
            pyglet.app.exit()

    # ---------- 命中测试 ----------

    def _pick(self, x, y):
        for n in reversed(self._hits):
            if n.interactive and n.hit(x, y):
                return n
        return None

    def _cursor(self, kind):
        name = CURSOR_MAP.get(kind, "CURSOR_DEFAULT")
        cur = self.window.get_system_mouse_cursor(getattr(self.window, name))
        self.window.set_mouse_cursor(cur)

    # ---------- 事件 ----------

    def on_mouse_press(self, x, y, button, modifiers):
        if button != mouse.LEFT:
            return
        ey = self.window.height - y
        n = self._pick(x, ey)
        self._pressed = n
        if n:
            n.on_press(x, ey)
            if n.focusable:
                if self.focus and self.focus is not n:
                    self.focus.on_blur()
                self.focus = n
                n.on_focus()
            return True
        if self.focus:
            self.focus.on_blur()
            self.focus = None
        return False

    def on_mouse_release(self, x, y, button, modifiers):
        ey = self.window.height - y
        n = self._pick(x, ey)
        if self._pressed is not None:
            if n is self._pressed:
                self._pressed.on_release(x, ey)
            else:
                self._pressed.pressed = False
        self._pressed = None

    def on_mouse_motion(self, x, y, dx, dy):
        self._track_hover(x, y)

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        self._track_hover(x, y)

    def _track_hover(self, x, y):
        ey = self.window.height - y
        n = self._pick(x, ey)
        if n is not self._hover:
            if self._hover is not None:
                self._hover.on_leave()
            self._hover = n
            if n is not None:
                n.on_enter()
        self._cursor(n.cursor if n is not None else None)

    def on_text(self, text):
        if self.focus is not None:
            return self.focus.on_text(text)
        return False

    def on_key_press(self, symbol, modifiers):
        if self.focus is not None and self.focus.on_key(symbol, modifiers):
            return True
        if symbol == pkey.ESCAPE and self.focus is not None:
            self.focus.on_blur()
            self.focus = None
            return True
        return False

    # ---------- 运行 ----------

    def screenshot(self, path):
        self._shot = path

    def run(self, after=None):
        if after:
            pyglet.clock.schedule_once(lambda dt: after(self), 0.6)
        pyglet.app.run()
