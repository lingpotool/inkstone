"""Windows WGL 驱动 —— ctypes 直调 opengl32 的真机 GL 实现（R8.2，docs/22）。

**为什么放在 backend（L0）而不是 gfx**：建窗口、拿设备上下文、创建 GL 上下文
是"碰平台"的事，与 SDL2 后端同一分层纪律。它实现的是 gfx 的 `GLDriver` 协议
（backend → gfx 是 `test_architecture.py` 已登记的例外，见 KNOWN_EXCEPTIONS）。

**为什么不用 SDL2**：离屏渲染不需要窗口系统集成——一个隐藏窗口 + WGL 上下文 +
FBO 就够了。这样即使没装 SDL2 动态库（本机就没有），GL 路径也能跑通、能测。
SDL2 只在"真窗口 present"时才需要（R8.4）。

**为什么用 ctypes 而不是 PyOpenGL**：铁律 5（不新增运行时依赖）。opengl32.dll
只导出 GL 1.1，1.2+ 的入口（VBO/FBO/shader）要用 `wglGetProcAddress` 取。

**离屏渲染的取舍**：帧目标是一张 FBO 纹理，不是窗口后备缓冲。这样 CI/无窗口
环境也能出图（只要机器有 GL 驱动），`read_pixels` 从 FBO 回读。真窗口上屏
（swap）留到 R8.4 接 `FrameRenderer`。

**坐标系**：显示列表是左上原点、y 向下；GL 是左下原点、y 向上。顶点着色器里
翻 y，`read_pixels` 再按行翻转回来——两处必须一致，测试用"画在左上角的方块
读回仍在左上角"钉住。

状态：已实现（R8.2 首个里程碑：实心/描边矩形 + 字形 + 读回）。
Linux GLX/EGL、macOS CGL 属后续（同一 `GLDriver` 协议）。
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any

__all__ = [
    "GLUnavailableError",
    "WglGLDriver",
    "sdl_gl_driver",
    "windows_gl_driver",
]

_VENDOR = 0x1F00
_RENDERER = 0x1F01
_VERSION = 0x1F02
_EXTENSIONS = 0x1F03

# 帧缓冲/纹理常量
_TEXTURE_2D = 0x0DE1
_TEXTURE0 = 0x84C0
_RGBA = 0x1908
_RED = 0x1903
_UNSIGNED_BYTE = 0x1401
_TEXTURE_MIN_FILTER = 0x2801
_TEXTURE_MAG_FILTER = 0x2800
_TEXTURE_WRAP_S = 0x2802
_TEXTURE_WRAP_T = 0x2803
_LINEAR = 0x2601
_CLAMP_TO_EDGE = 0x812F
_FRAMEBUFFER = 0x8D40
_COLOR_ATTACHMENT0 = 0x8CE0
_FRAMEBUFFER_COMPLETE = 0x8CD5
_ARRAY_BUFFER = 0x8892
_STREAM_DRAW = 0x88E0
_STATIC_DRAW = 0x88E4
_FLOAT = 0x1406
_TRIANGLES = 0x0004
_BLEND = 0x0BE2
_SRC_ALPHA = 0x0302
_ONE_MINUS_SRC_ALPHA = 0x0303
_SCISSOR_TEST = 0x0C11
_COLOR_BUFFER_BIT = 0x00004000
_VERTEX_SHADER = 0x8B31
_FRAGMENT_SHADER = 0x8B30
_COMPILE_STATUS = 0x8B81
_LINK_STATUS = 0x8B82
_UNPACK_ALIGNMENT = 0x0CF5
_PACK_ALIGNMENT = 0x0D05
_RGBA8 = 0x8058
_MAX_TEXTURE_SIZE = 0x0D33

#: "还没设过 scissor"的哨兵——与 None（=关闭裁剪）必须区分开
_UNSET: Any = object()


class GLUnavailableError(RuntimeError):
    """拿不到 GL 上下文（非 Windows、驱动缺失、或 FBO 不完整）。"""


# 矩形合批：每个矩形的 center/half/radius/ring/color 都做成**顶点属性**，
# 于是同一次 scissor 下的所有矩形能合成一次 glDrawArrays（R8.3）。
# 用 uniform 的话每个矩形都得单独一次 draw（uniform 是 program 级状态），
# 全屏 97 条指令就是 97 次 draw + 97 次 VBO 上传——实测 0.25ms/指令。
_SDF_VERTEX = b"""#version 120
attribute vec2 a_pos;
attribute vec2 a_local;
attribute vec2 a_half;
attribute float a_radius;
attribute float a_ring;
attribute vec4 a_color;
uniform vec2 u_viewport;
varying vec2 v_local;
varying vec2 v_half;
varying float v_radius;
varying float v_ring;
varying vec4 v_color;
void main() {
    vec2 clip = (a_pos / u_viewport) * 2.0 - 1.0;
    gl_Position = vec4(clip.x, -clip.y, 0.0, 1.0);
    v_local = a_local;
    v_half = a_half;
    v_radius = a_radius;
    v_ring = a_ring;
    v_color = a_color;
}
"""

_SDF_FRAGMENT = b"""#version 120
varying vec2 v_local;
varying vec2 v_half;
varying float v_radius;
varying float v_ring;
varying vec4 v_color;
float sd_round_rect(vec2 p, vec2 b, float r) {
    vec2 q = abs(p) - (b - r);
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}
void main() {
    float d = sd_round_rect(v_local, v_half, v_radius);
    float coverage = clamp(0.5 - d, 0.0, 1.0);   // 1px analytic antialiasing
    if (v_ring > 0.0) {
        // stroke grows inward: inside outer edge, outside inner edge
        coverage = min(coverage, clamp(0.5 + (d + v_ring), 0.0, 1.0));
    }
    gl_FragColor = vec4(v_color.rgb, v_color.a * coverage);
}
"""

#: 每个矩形顶点的浮点数个数：pos(2)+local(2)+half(2)+radius(1)+ring(1)+color(4)
_RECT_FLOATS = 12

# 字形走**图集 + 合批**（R8.3）：所有字形共用一张 GL_RED 图集纹理，
# 每个字形只贡献一个带 uv 的四边形，整段 run 一次 draw。
# 不用图集的话每个字形要单独绑纹理 + 一次 draw（实测 37µs/字形）。
_GLYPH_VERTEX = b"""#version 120
attribute vec2 a_pos;
attribute vec2 a_uv;
attribute vec4 a_color;
uniform vec2 u_viewport;
varying vec2 v_uv;
varying vec4 v_color;
void main() {
    vec2 clip = (a_pos / u_viewport) * 2.0 - 1.0;
    gl_Position = vec4(clip.x, -clip.y, 0.0, 1.0);
    v_uv = a_uv;
    v_color = a_color;
}
"""

_GLYPH_FRAGMENT = b"""#version 120
uniform sampler2D u_texture;
varying vec2 v_uv;
varying vec4 v_color;
void main() {
    float coverage = texture2D(u_texture, v_uv).r;
    gl_FragColor = vec4(v_color.rgb, v_color.a * coverage);
}
"""

# 层缓存合成：把一层已经渲染好的 RGBA 纹理画成一个四边形（R8.4 RasterCache）
_LAYER_VERTEX = b"""#version 120
attribute vec2 a_pos;
attribute vec2 a_uv;
uniform vec2 u_viewport;
varying vec2 v_uv;
void main() {
    vec2 clip = (a_pos / u_viewport) * 2.0 - 1.0;
    gl_Position = vec4(clip.x, -clip.y, 0.0, 1.0);
    v_uv = a_uv;
}
"""

_LAYER_FRAGMENT = b"""#version 120
uniform sampler2D u_texture;
varying vec2 v_uv;
void main() {
    gl_FragColor = texture2D(u_texture, v_uv);
}
"""

#: 每个字形顶点的浮点数个数：pos(2)+uv(2)+color(4)
_GLYPH_FLOATS = 8
#: 字形图集边长（texel）。2048² = 4MB 单通道，够 CJK 常用字；
#: 满了就清空重排（见 `_atlas_uv`），不会无限增长。
_ATLAS_SIZE = 2048


class _GL:
    """加载 opengl32 + wglGetProcAddress 的入口表。

    `opengl32.dll` 的导出只有 GL 1.1；VBO / FBO / shader 这些 1.2+ 的入口必须
    逐个问 `wglGetProcAddress`。取不到的入口**当场抛**——静默留一个 NULL 指针，
    会在第一次 draw 时以访问违例的形式炸掉，比报错难查得多。
    """

    def __init__(self, get_proc: Any = None) -> None:
        """`get_proc(name: bytes) -> int | None` 是"取 GL 入口地址"的策略。

        默认用 `wglGetProcAddress`（自建 WGL 上下文）；挂到 SDL 上下文时传
        `SDL_GL_GetProcAddress`——**换的是取地址的方式，不是整套绑定**。
        核心 1.1 函数始终从 `opengl32.dll` 取（它在 WGL 上下文下始终有效）。
        """
        if sys.platform != "win32":
            raise GLUnavailableError("WGL 驱动只在 Windows 上可用")
        try:
            self.opengl32 = ctypes.WinDLL("opengl32")
            self.gdi32 = ctypes.WinDLL("gdi32")
            self.user32 = ctypes.WinDLL("user32")
        except OSError as exc:  # pragma: no cover - 取决于机器
            raise GLUnavailableError(f"加载 opengl32 失败：{exc}") from exc
        self._get_proc = get_proc
        self._load_core()
        # 1.2+ 的入口要等上下文 current 之后才能取，所以扩展入口由驱动在建好
        # 上下文后调用 `load_extensions()` 加载。

    # -------------------------------------------------------- 加载

    def _core(self, name: str, res: Any, *args: Any) -> Any:
        fn = getattr(self.opengl32, name)
        fn.restype = res
        fn.argtypes = list(args)
        return fn

    def _ext(self, name: str, res: Any, *args: Any) -> Any:
        if self._get_proc is not None:
            addr = self._get_proc(name.encode())
        else:
            self.opengl32.wglGetProcAddress.restype = ctypes.c_void_p
            self.opengl32.wglGetProcAddress.argtypes = [ctypes.c_char_p]
            addr = self.opengl32.wglGetProcAddress(name.encode())
        if not addr:
            raise GLUnavailableError(f"驱动没有提供 {name}")
        fn = ctypes.CFUNCTYPE(res, *args)(addr)
        return fn

    def _load_core(self) -> None:
        self.glGetString = self._core("glGetString", ctypes.c_char_p, ctypes.c_uint)
        self.glGetError = self._core("glGetError", ctypes.c_uint)
        self.glViewport = self._core(
            "glViewport", None, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int
        )
        self.glClearColor = self._core(
            "glClearColor", None, ctypes.c_float, ctypes.c_float, ctypes.c_float, ctypes.c_float
        )
        self.glClear = self._core("glClear", None, ctypes.c_uint)
        self.glEnable = self._core("glEnable", None, ctypes.c_uint)
        self.glDisable = self._core("glDisable", None, ctypes.c_uint)
        self.glScissor = self._core(
            "glScissor", None, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int
        )
        self.glBlendFunc = self._core("glBlendFunc", None, ctypes.c_uint, ctypes.c_uint)
        self.glGenTextures = self._core(
            "glGenTextures", None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint)
        )
        self.glDeleteTextures = self._core(
            "glDeleteTextures", None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint)
        )
        self.glBindTexture = self._core("glBindTexture", None, ctypes.c_uint, ctypes.c_uint)
        self.glTexImage2D = self._core(
            "glTexImage2D",
            None,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_void_p,
        )
        self.glTexParameteri = self._core(
            "glTexParameteri", None, ctypes.c_uint, ctypes.c_uint, ctypes.c_int
        )
        self.glPixelStorei = self._core("glPixelStorei", None, ctypes.c_uint, ctypes.c_int)
        self.glReadPixels = self._core(
            "glReadPixels",
            None,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_void_p,
        )
        self.glDrawArrays = self._core(
            "glDrawArrays", None, ctypes.c_uint, ctypes.c_int, ctypes.c_int
        )
        self.glTexSubImage2D = self._core(
            "glTexSubImage2D",
            None,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_void_p,
        )

    def load_extensions(self) -> None:
        """加载 GL 1.2+ 入口。**必须在上下文 current 之后调用**。"""
        c = ctypes
        self.glGenBuffers = self._ext("glGenBuffers", None, c.c_int, c.POINTER(c.c_uint))
        self.glDeleteBuffers = self._ext("glDeleteBuffers", None, c.c_int, c.POINTER(c.c_uint))
        self.glBindBuffer = self._ext("glBindBuffer", None, c.c_uint, c.c_uint)
        self.glBufferData = self._ext(
            "glBufferData", None, c.c_uint, c.c_ssize_t, c.c_void_p, c.c_uint
        )
        self.glEnableVertexAttribArray = self._ext("glEnableVertexAttribArray", None, c.c_uint)
        self.glDisableVertexAttribArray = self._ext("glDisableVertexAttribArray", None, c.c_uint)
        self.glVertexAttribPointer = self._ext(
            "glVertexAttribPointer",
            None,
            c.c_uint,
            c.c_int,
            c.c_uint,
            c.c_ubyte,
            c.c_int,
            c.c_void_p,
        )
        self.glCreateShader = self._ext("glCreateShader", c.c_uint, c.c_uint)
        self.glShaderSource = self._ext(
            "glShaderSource", None, c.c_uint, c.c_int, c.POINTER(c.c_char_p), c.POINTER(c.c_int)
        )
        self.glCompileShader = self._ext("glCompileShader", None, c.c_uint)
        self.glGetShaderiv = self._ext(
            "glGetShaderiv", None, c.c_uint, c.c_uint, c.POINTER(c.c_int)
        )
        self.glGetShaderInfoLog = self._ext(
            "glGetShaderInfoLog", None, c.c_uint, c.c_int, c.POINTER(c.c_int), c.c_char_p
        )
        self.glDeleteShader = self._ext("glDeleteShader", None, c.c_uint)
        self.glCreateProgram = self._ext("glCreateProgram", c.c_uint)
        self.glAttachShader = self._ext("glAttachShader", None, c.c_uint, c.c_uint)
        self.glLinkProgram = self._ext("glLinkProgram", None, c.c_uint)
        self.glGetProgramiv = self._ext(
            "glGetProgramiv", None, c.c_uint, c.c_uint, c.POINTER(c.c_int)
        )
        self.glGetProgramInfoLog = self._ext(
            "glGetProgramInfoLog", None, c.c_uint, c.c_int, c.POINTER(c.c_int), c.c_char_p
        )
        self.glUseProgram = self._ext("glUseProgram", None, c.c_uint)
        self.glGetUniformLocation = self._ext("glGetUniformLocation", c.c_int, c.c_uint, c.c_char_p)
        self.glUniform1f = self._ext("glUniform1f", None, c.c_int, c.c_float)
        self.glUniform2f = self._ext("glUniform2f", None, c.c_int, c.c_float, c.c_float)
        self.glUniform4f = self._ext(
            "glUniform4f", None, c.c_int, c.c_float, c.c_float, c.c_float, c.c_float
        )
        self.glUniform1i = self._ext("glUniform1i", None, c.c_int, c.c_int)
        self.glGetAttribLocation = self._ext("glGetAttribLocation", c.c_int, c.c_uint, c.c_char_p)
        self.glGenFramebuffers = self._ext("glGenFramebuffers", None, c.c_int, c.POINTER(c.c_uint))
        self.glDeleteFramebuffers = self._ext(
            "glDeleteFramebuffers", None, c.c_int, c.POINTER(c.c_uint)
        )
        self.glBindFramebuffer = self._ext("glBindFramebuffer", None, c.c_uint, c.c_uint)
        self.glFramebufferTexture2D = self._ext(
            "glFramebufferTexture2D", None, c.c_uint, c.c_uint, c.c_uint, c.c_uint, c.c_int
        )
        self.glCheckFramebufferStatus = self._ext("glCheckFramebufferStatus", c.c_uint, c.c_uint)
        self.glActiveTexture = self._ext("glActiveTexture", None, c.c_uint)


class _Program:
    def __init__(self, gl: _GL, vertex: bytes, fragment: bytes) -> None:
        self._gl = gl
        self.id = self._build(vertex, fragment)
        # uniform/attribute 位置查询有字符串哈希开销，且每帧每指令都会问到——
        # 位置在 program 生命周期内不变，查一次记住（热路径的基本功）。
        self._uniforms: dict[bytes, int] = {}
        self._attribs: dict[bytes, int] = {}

    def uniform(self, name: bytes) -> int:
        location = self._uniforms.get(name)
        if location is None:
            location = int(self._gl.glGetUniformLocation(self.id, name))
            self._uniforms[name] = location
        return location

    def attrib(self, name: bytes) -> int:
        location = self._attribs.get(name)
        if location is None:
            location = int(self._gl.glGetAttribLocation(self.id, name))
            self._attribs[name] = location
        return location

    def _build(self, vertex: bytes, fragment: bytes) -> int:
        gl = self._gl
        vs = self._compile(_VERTEX_SHADER, vertex)
        fs = self._compile(_FRAGMENT_SHADER, fragment)
        program = gl.glCreateProgram()
        gl.glAttachShader(program, vs)
        gl.glAttachShader(program, fs)
        gl.glLinkProgram(program)
        status = ctypes.c_int(0)
        gl.glGetProgramiv(program, _LINK_STATUS, ctypes.byref(status))
        if not status.value:
            log = ctypes.create_string_buffer(4096)
            gl.glGetProgramInfoLog(program, 4096, None, log)
            raise GLUnavailableError(f"着色器链接失败：{log.value.decode(errors='replace')}")
        gl.glDeleteShader(vs)
        gl.glDeleteShader(fs)
        return int(program)

    def _compile(self, kind: int, source: bytes) -> int:
        gl = self._gl
        shader = gl.glCreateShader(kind)
        buf = ctypes.c_char_p(source)
        length = ctypes.c_int(len(source))
        gl.glShaderSource(shader, 1, ctypes.byref(buf), ctypes.byref(length))
        gl.glCompileShader(shader)
        status = ctypes.c_int(0)
        gl.glGetShaderiv(shader, _COMPILE_STATUS, ctypes.byref(status))
        if not status.value:
            log = ctypes.create_string_buffer(4096)
            gl.glGetShaderInfoLog(shader, 4096, None, log)
            raise GLUnavailableError(f"着色器编译失败：{log.value.decode(errors='replace')}")
        return int(shader)


class WglGLDriver:
    """Windows 上的 GL 驱动：离屏（隐藏窗口 + WGL + FBO）或挂到已有 SDL 上下文。"""

    _gl: _GL

    def __init__(self, *, attach: tuple[Any, Any, Any] | None = None) -> None:
        """两种构造方式。

        - 默认：自建**隐藏窗口 + WGL 上下文**，离屏渲染（截图/黄金图/基准）；
        - `attach=(gl, present, make_current)`：挂到**调用方已有的 GL 上下文**
          （SDL 窗口，`sdl_gl_driver` 用）。`present` 换链、`make_current`
          在释放资源前恢复当前上下文。
        两条路共用同一套绘制/缓存逻辑——上下文怎么来不该影响画法。
        """
        if attach is not None:
            gl, present, make_current = attach
            self._init_fields()
            self._finish_setup(gl, present, make_current)
            return
        self._init_fields()
        self._gl = _GL()
        self._create_context()
        self._finish_setup(self._gl, None)

    def _init_fields(self) -> None:
        self._present_cb: Any = None
        self._make_current_cb: Any = None
        self._detach_cb: Any = None
        self._attached = False
        self._hwnd = 0
        self._hdc = 0
        self._context = 0
        self._fb = 0
        self._color_texture = 0
        self._vbo = 0
        self._rect_vbo = 0
        #: 矩形合批缓冲（每顶点 12 个 float，见 `_RECT_FLOATS`）
        self._rect_vertices: list[float] = []
        #: 字形合批缓冲（每顶点 8 个 float，见 `_GLYPH_FLOATS`）
        self._glyph_vertices: list[float] = []
        #: 字形图集：内容键 → (u0, v0, u1, v1) 归一化 uv
        self._atlas: dict[tuple[int, int, int, int, bytes], tuple[float, float, float, float]] = {}
        self._atlas_texture = 0
        self._atlas_x = 0
        self._atlas_y = 0
        self._atlas_row_h = 0
        #: 当前 scissor；`_UNSET` 表示"还没设过"，用于跳过重复的 GL 状态调用
        self._scissor: Any = _UNSET
        self._width = 0
        self._height = 0
        self._textures: dict[int, int] = {}
        #: 层缓存 key → (fbo, texture, width, height)，FIFO 淘汰
        self._layers: dict[int, tuple[int, int, int, int]] = {}
        self._layer_limit = 32
        self._layer_active: tuple[int, int, int, int] | None = None
        #: 层渲染时的坐标原点偏移（层内坐标 = 绝对坐标 - origin）
        self._origin = (0.0, 0.0)

    def _finish_setup(self, gl: _GL, present: Any, make_current: Any = None) -> None:
        """上下文就绪之后的所有 GL 装配（两条构造路径共用）。"""
        self._gl = gl
        gl.load_extensions()
        self._sdf = _Program(gl, _SDF_VERTEX, _SDF_FRAGMENT)
        self._glyph = _Program(gl, _GLYPH_VERTEX, _GLYPH_FRAGMENT)
        self._layer_program = _Program(gl, _LAYER_VERTEX, _LAYER_FRAGMENT)
        self._create_quad_buffer()
        if present is not None:
            self._present_cb = present
            self._make_current_cb = make_current
            self._attached = True

    # ------------------------------------------------------------ 可用性

    @staticmethod
    def is_available() -> bool:
        try:
            driver = WglGLDriver()
        except Exception:
            return False
        driver.close()
        return True

    # ------------------------------------------------------------ 上下文

    def _create_context(self) -> None:
        user32, gdi32 = self._gl.user32, self._gl.gdi32
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        user32.DefWindowProcW.restype = ctypes.c_void_p
        user32.DefWindowProcW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        ]
        user32.CreateWindowExW.restype = ctypes.c_void_p
        user32.CreateWindowExW.argtypes = [
            ctypes.c_uint,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        user32.GetDC.restype = ctypes.c_void_p
        user32.GetDC.argtypes = [ctypes.c_void_p]
        gdi32.ChoosePixelFormat.restype = ctypes.c_int
        gdi32.ChoosePixelFormat.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gdi32.SetPixelFormat.restype = ctypes.c_int
        gdi32.SetPixelFormat.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        self._gl.opengl32.wglCreateContext.restype = ctypes.c_void_p
        self._gl.opengl32.wglCreateContext.argtypes = [ctypes.c_void_p]
        self._gl.opengl32.wglMakeCurrent.restype = ctypes.c_int
        self._gl.opengl32.wglMakeCurrent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        class _WndClass(ctypes.Structure):
            _fields_ = [
                ("style", ctypes.c_uint),
                ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", ctypes.c_void_p),
                ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p),
            ]

        class _PixelFormat(ctypes.Structure):
            _fields_ = [
                ("nSize", ctypes.c_ushort),
                ("nVersion", ctypes.c_ushort),
                ("dwFlags", ctypes.c_uint),
                ("iPixelType", ctypes.c_ubyte),
                ("cColorBits", ctypes.c_ubyte),
                ("cRedBits", ctypes.c_ubyte),
                ("cRedShift", ctypes.c_ubyte),
                ("cGreenBits", ctypes.c_ubyte),
                ("cGreenShift", ctypes.c_ubyte),
                ("cBlueBits", ctypes.c_ubyte),
                ("cBlueShift", ctypes.c_ubyte),
                ("cAlphaBits", ctypes.c_ubyte),
                ("cAlphaShift", ctypes.c_ubyte),
                ("cAccumBits", ctypes.c_ubyte),
                ("cAccumRedBits", ctypes.c_ubyte),
                ("cAccumGreenBits", ctypes.c_ubyte),
                ("cAccumBlueBits", ctypes.c_ubyte),
                ("cAccumAlphaBits", ctypes.c_ubyte),
                ("cDepthBits", ctypes.c_ubyte),
                ("cStencilBits", ctypes.c_ubyte),
                ("cAuxBuffers", ctypes.c_ubyte),
                ("iLayerType", ctypes.c_ubyte),
                ("bReserved", ctypes.c_ubyte),
                ("dwLayerMask", ctypes.c_uint),
                ("dwVisibleMask", ctypes.c_uint),
                ("dwDamageMask", ctypes.c_uint),
            ]

        instance = kernel32.GetModuleHandleW(None)
        wc = _WndClass()
        wc.lpszClassName = "inkstone_gl_offscreen"
        wc.hInstance = instance
        wc.lpfnWndProc = ctypes.cast(user32.DefWindowProcW, ctypes.c_void_p)
        if not user32.RegisterClassW(ctypes.byref(wc)):
            # 已注册过不算错；真正的失败会在 CreateWindow 上暴露
            pass
        self._hwnd = user32.CreateWindowExW(
            0, wc.lpszClassName, "inkstone-gl", 0, 0, 0, 8, 8, None, None, instance, None
        )
        if not self._hwnd:
            raise GLUnavailableError("创建离屏窗口失败")
        self._hdc = user32.GetDC(self._hwnd)
        pfd = _PixelFormat()
        pfd.nSize = ctypes.sizeof(_PixelFormat)
        pfd.nVersion = 1
        # SUPPORT_OPENGL | DOUBLEBUFFER
        pfd.dwFlags = 0x00000004 | 0x00000020
        pfd.cColorBits = 32
        pfd.cAlphaBits = 8
        fmt = gdi32.ChoosePixelFormat(self._hdc, ctypes.byref(pfd))
        if fmt == 0 or not gdi32.SetPixelFormat(self._hdc, fmt, ctypes.byref(pfd)):
            raise GLUnavailableError("没有可用的像素格式（OpenGL 支持缺失）")
        self._context = self._gl.opengl32.wglCreateContext(self._hdc)
        if not self._context:
            raise GLUnavailableError("wglCreateContext 失败")
        if not self._gl.opengl32.wglMakeCurrent(self._hdc, self._context):
            raise GLUnavailableError("wglMakeCurrent 失败")
        version = self._gl.glGetString(_VERSION)
        if version is None:
            raise GLUnavailableError("拿不到 GL 版本，驱动可能只支持软件回退")

    def _create_quad_buffer(self) -> None:
        vbo = ctypes.c_uint(0)
        self._gl.glGenBuffers(1, ctypes.byref(vbo))
        self._vbo = vbo.value
        rect_vbo = ctypes.c_uint(0)
        self._gl.glGenBuffers(1, ctypes.byref(rect_vbo))
        self._rect_vbo = rect_vbo.value

    # ------------------------------------------------------------ 帧目标

    def _ensure_target(self, width: int, height: int) -> None:
        if self._fb and self._width == width and self._height == height:
            return
        gl = self._gl
        if self._fb:
            fb = ctypes.c_uint(self._fb)
            gl.glDeleteFramebuffers(1, ctypes.byref(fb))
            tex = ctypes.c_uint(self._color_texture)
            gl.glDeleteTextures(1, ctypes.byref(tex))
        texture = ctypes.c_uint(0)
        gl.glGenTextures(1, ctypes.byref(texture))
        gl.glBindTexture(_TEXTURE_2D, texture.value)
        gl.glTexImage2D(_TEXTURE_2D, 0, _RGBA8, width, height, 0, _RGBA, _UNSIGNED_BYTE, None)
        gl.glTexParameteri(_TEXTURE_2D, _TEXTURE_MIN_FILTER, _LINEAR)
        gl.glTexParameteri(_TEXTURE_2D, _TEXTURE_MAG_FILTER, _LINEAR)
        gl.glTexParameteri(_TEXTURE_2D, _TEXTURE_WRAP_S, _CLAMP_TO_EDGE)
        gl.glTexParameteri(_TEXTURE_2D, _TEXTURE_WRAP_T, _CLAMP_TO_EDGE)
        fb = ctypes.c_uint(0)
        gl.glGenFramebuffers(1, ctypes.byref(fb))
        gl.glBindFramebuffer(_FRAMEBUFFER, fb.value)
        gl.glFramebufferTexture2D(_FRAMEBUFFER, _COLOR_ATTACHMENT0, _TEXTURE_2D, texture.value, 0)
        status = gl.glCheckFramebufferStatus(_FRAMEBUFFER)
        if status != _FRAMEBUFFER_COMPLETE:
            raise GLUnavailableError(f"FBO 不完整（0x{status:04X}）")
        self._fb, self._color_texture = fb.value, texture.value
        self._width, self._height = width, height
        gl.glBindTexture(_TEXTURE_2D, 0)

    # ------------------------------------------------------------ GLDriver 协议

    def begin(self, width_px: int, height_px: int, scale: float) -> None:
        del scale
        if self._context:
            self._gl.opengl32.wglMakeCurrent(self._hdc, self._context)
        elif self._make_current_cb is not None:
            # 挂载模式：上下文归 SDL，确保它在本线程 current
            # （直接调 wglMakeCurrent(0,0) 会把它解绑，之后所有 GL 调用静默失败）
            self._make_current_cb()
        self._ensure_target(width_px, height_px)
        self._gl.glViewport(0, 0, width_px, height_px)
        self._gl.glDisable(_SCISSOR_TEST)
        self._gl.glEnable(_BLEND)
        self._gl.glBlendFunc(_SRC_ALPHA, _ONE_MINUS_SRC_ALPHA)
        # 帧首复位合批状态：上一帧若在异常路径里没冲干净，这里兜住
        self._rect_vertices.clear()
        self._scissor = _UNSET
        # 注意：这里**不清屏**——清屏由 clear() 显式做，好让上层能整帧跳过（帧去重）

    def clear(self, color: Any) -> None:
        self._gl.glClearColor(color.r / 255.0, color.g / 255.0, color.b / 255.0, float(color.a))
        self._gl.glClear(_COLOR_BUFFER_BIT)

    # ------------------------------------------------------------ 层缓存

    def layer_begin(
        self, key: int, width: int, height: int, origin_x: float, origin_y: float
    ) -> bool:
        self._flush_rects()
        self._flush_glyphs()
        if key in self._layers:
            return False  # 命中缓存：调用方跳过渲染，稍后直接 draw_layer
        gl = self._gl
        texture = ctypes.c_uint(0)
        gl.glGenTextures(1, ctypes.byref(texture))
        gl.glBindTexture(_TEXTURE_2D, texture.value)
        gl.glTexImage2D(_TEXTURE_2D, 0, _RGBA8, width, height, 0, _RGBA, _UNSIGNED_BYTE, None)
        gl.glTexParameteri(_TEXTURE_2D, _TEXTURE_MIN_FILTER, _LINEAR)
        gl.glTexParameteri(_TEXTURE_2D, _TEXTURE_MAG_FILTER, _LINEAR)
        gl.glTexParameteri(_TEXTURE_2D, _TEXTURE_WRAP_S, _CLAMP_TO_EDGE)
        gl.glTexParameteri(_TEXTURE_2D, _TEXTURE_WRAP_T, _CLAMP_TO_EDGE)
        gl.glBindTexture(_TEXTURE_2D, 0)
        fb = ctypes.c_uint(0)
        gl.glGenFramebuffers(1, ctypes.byref(fb))
        gl.glBindFramebuffer(_FRAMEBUFFER, fb.value)
        gl.glFramebufferTexture2D(_FRAMEBUFFER, _COLOR_ATTACHMENT0, _TEXTURE_2D, texture.value, 0)
        status = gl.glCheckFramebufferStatus(_FRAMEBUFFER)
        if status != _FRAMEBUFFER_COMPLETE:
            raise GLUnavailableError(f"层 FBO 不完整（0x{status:04X}）")
        if len(self._layers) >= self._layer_limit:
            oldest = next(iter(self._layers))
            self._delete_layer(oldest)
        self._layers[key] = (fb.value, texture.value, width, height)
        gl.glViewport(0, 0, width, height)
        gl.glDisable(_SCISSOR_TEST)
        gl.glClearColor(0.0, 0.0, 0.0, 0.0)
        gl.glClear(_COLOR_BUFFER_BIT)
        self._layer_active = (fb.value, texture.value, width, height)
        self._origin = (origin_x, origin_y)
        return True

    def layer_end(self) -> None:
        self._flush_rects()
        self._flush_glyphs()
        gl = self._gl
        self._layer_active = None
        self._origin = (0.0, 0.0)
        gl.glBindFramebuffer(_FRAMEBUFFER, self._fb)
        gl.glViewport(0, 0, self._width, self._height)
        gl.glDisable(_SCISSOR_TEST)

    def draw_layer(self, key: int, rect: Any) -> None:
        self._flush_rects()
        self._flush_glyphs()
        cached = self._layers.get(key)
        if cached is None:
            return
        _, texture, _, _ = cached
        gl = self._gl
        program = self._layer_program
        gl.glUseProgram(program.id)
        vw, vh = self._viewport_size()
        gl.glUniform2f(program.uniform(b"u_viewport"), float(vw), float(vh))
        gl.glUniform1i(program.uniform(b"u_texture"), 0)
        gl.glActiveTexture(_TEXTURE0)
        gl.glBindTexture(_TEXTURE_2D, texture)
        # 层纹理的行序：内容顶边落在纹理 t=1（渲染时 y 已翻），所以采样要翻 v
        gl.glBindBuffer(_ARRAY_BUFFER, self._vbo)
        x0, y0 = rect.left, rect.top
        x1, y1 = rect.right, rect.bottom
        vertices = (
            x0,
            y0,
            0.0,
            1.0,
            x1,
            y0,
            1.0,
            1.0,
            x1,
            y1,
            1.0,
            0.0,
            x0,
            y0,
            0.0,
            1.0,
            x1,
            y1,
            1.0,
            0.0,
            x0,
            y1,
            0.0,
            0.0,
        )
        array = (ctypes.c_float * len(vertices))(*vertices)
        gl.glBufferData(_ARRAY_BUFFER, ctypes.sizeof(array), array, _STREAM_DRAW)
        stride = 4 * ctypes.sizeof(ctypes.c_float)
        for name, offset in ((b"a_pos", 0), (b"a_uv", 8)):
            location = program.attrib(name)
            if location >= 0:
                gl.glEnableVertexAttribArray(location)
                gl.glVertexAttribPointer(location, 2, _FLOAT, 0, stride, ctypes.c_void_p(offset))
        gl.glDrawArrays(_TRIANGLES, 0, 6)
        gl.glBindBuffer(_ARRAY_BUFFER, 0)
        gl.glBindTexture(_TEXTURE_2D, 0)
        gl.glUseProgram(0)

    def _delete_layer(self, key: int) -> None:
        cached = self._layers.pop(key, None)
        if cached is None:
            return
        fb, texture, _, _ = cached
        fb_id = ctypes.c_uint(fb)
        tex_id = ctypes.c_uint(texture)
        self._gl.glDeleteFramebuffers(1, ctypes.byref(fb_id))
        self._gl.glDeleteTextures(1, ctypes.byref(tex_id))

    def _viewport_size(self) -> tuple[int, int]:
        if self._layer_active is not None:
            return self._layer_active[2], self._layer_active[3]
        return self._width, self._height

    def end(self) -> None:
        """冲批并结束一帧。挂了窗口（`_present_cb`）时把帧内容 blit 上屏并换链。"""
        self._flush_rects()
        self._flush_glyphs()
        if self._present_cb is not None:
            self._present()
            self._present_cb()

    def _present(self) -> None:
        """把离屏 FBO 的内容画到窗口默认帧缓冲（R8.6）。

        为什么绕这一道而不是直接画进默认帧缓冲：层缓存、读回、尺寸口径都建立在
        FBO 上；上屏只是最后一步 blit——这样"窗口"与"离屏"两条路画出来的是同一张图。
        纹理行序沿用渲染时的 y 翻转约定，所以采样要翻 v（与 `draw_layer` 一致）。
        """
        gl = self._gl
        gl.glBindFramebuffer(_FRAMEBUFFER, 0)
        gl.glViewport(0, 0, self._width, self._height)
        gl.glDisable(_SCISSOR_TEST)
        program = self._layer_program
        gl.glUseProgram(program.id)
        gl.glUniform2f(program.uniform(b"u_viewport"), float(self._width), float(self._height))
        gl.glUniform1i(program.uniform(b"u_texture"), 0)
        gl.glActiveTexture(_TEXTURE0)
        gl.glBindTexture(_TEXTURE_2D, self._color_texture)
        x0, y0, x1, y1 = 0.0, 0.0, float(self._width), float(self._height)
        vertices = (
            x0,
            y0,
            0.0,
            1.0,
            x1,
            y0,
            1.0,
            1.0,
            x1,
            y1,
            1.0,
            0.0,
            x0,
            y0,
            0.0,
            1.0,
            x1,
            y1,
            1.0,
            0.0,
            x0,
            y1,
            0.0,
            0.0,
        )
        gl.glBindBuffer(_ARRAY_BUFFER, self._vbo)
        array = (ctypes.c_float * len(vertices))(*vertices)
        gl.glBufferData(_ARRAY_BUFFER, ctypes.sizeof(array), array, _STREAM_DRAW)
        stride = 4 * ctypes.sizeof(ctypes.c_float)
        for name, offset in ((b"a_pos", 0), (b"a_uv", 8)):
            location = program.attrib(name)
            if location >= 0:
                gl.glEnableVertexAttribArray(location)
                gl.glVertexAttribPointer(location, 2, _FLOAT, 0, stride, ctypes.c_void_p(offset))
        gl.glDrawArrays(_TRIANGLES, 0, 6)
        gl.glBindBuffer(_ARRAY_BUFFER, 0)
        gl.glBindTexture(_TEXTURE_2D, 0)
        gl.glUseProgram(0)
        # 回到离屏 FBO：screenshot() 从它读回，换链后默认帧缓冲内容不再保证
        gl.glBindFramebuffer(_FRAMEBUFFER, self._fb)
        gl.glViewport(0, 0, self._width, self._height)

    def set_scissor(self, rect: Any) -> None:
        if rect == self._scissor:
            return
        # scissor 与绘制顺序绑定：换裁剪前必须把上一批画掉，否则它们会被新 scissor 裁错
        self._flush_rects()
        self._flush_glyphs()
        self._scissor = rect
        gl = self._gl
        if rect is None:
            gl.glDisable(_SCISSOR_TEST)
            return
        gl.glEnable(_SCISSOR_TEST)
        if self._layer_active is not None:
            ox, oy = self._origin
            layer_h = self._layer_active[3]
            x = int(rect.left - ox)
            width = int(rect.right - ox) - x
            height = int(rect.bottom - oy) - int(rect.top - oy)
            y = layer_h - int(rect.bottom - oy)
        else:
            x = int(rect.left)
            width = int(rect.right) - x
            height = int(rect.bottom) - int(rect.top)
            # GL 原点在左下：把 top 换算成底边
            y = self._height - int(rect.bottom)
        gl.glScissor(x, max(0, y), max(0, width), max(0, height))

    def fill_rect(self, rect: Any, radius: float, color: Any) -> None:
        self._queue_rect(rect, radius, color, ring=0.0)

    def stroke_rect(self, rect: Any, radius: float, width: float, color: Any) -> None:
        self._queue_rect(rect, radius, color, ring=width)

    def _queue_rect(self, rect: Any, radius: float, color: Any, ring: float) -> None:
        """把一个矩形写进合批缓冲。真正下 draw 在 `_flush_rects`。"""
        # 混合顺序相关：矩形与字形交替时必须先画已排队的字形
        self._flush_glyphs()
        ox, oy = self._origin
        hx, hy = rect.width / 2.0, rect.height / 2.0
        r, g, b, a = color.r / 255.0, color.g / 255.0, color.b / 255.0, float(color.a)
        x0, y0 = rect.left - ox, rect.top - oy
        x1, y1 = rect.right - ox, rect.bottom - oy
        # 两个三角形（6 顶点），每顶点：pos, local, half, radius, ring, color
        corners = (
            (x0, y0, -hx, -hy),
            (x1, y0, hx, -hy),
            (x1, y1, hx, hy),
            (x0, y0, -hx, -hy),
            (x1, y1, hx, hy),
            (x0, y1, -hx, hy),
        )
        for px, py, lx, ly in corners:
            self._rect_vertices.extend((px, py, lx, ly, hx, hy, radius, ring, r, g, b, a))

    def _flush_rects(self) -> None:
        if not self._rect_vertices:
            return
        gl = self._gl
        program = self._sdf
        array = (ctypes.c_float * len(self._rect_vertices))(*self._rect_vertices)
        gl.glUseProgram(program.id)
        vw, vh = self._viewport_size()
        gl.glUniform2f(program.uniform(b"u_viewport"), float(vw), float(vh))
        gl.glBindBuffer(_ARRAY_BUFFER, self._rect_vbo)
        gl.glBufferData(_ARRAY_BUFFER, ctypes.sizeof(array), array, _STREAM_DRAW)
        self._bind_rect_attributes(program)
        gl.glDrawArrays(_TRIANGLES, 0, len(self._rect_vertices) // _RECT_FLOATS)
        gl.glBindBuffer(_ARRAY_BUFFER, 0)
        gl.glUseProgram(0)
        self._rect_vertices.clear()

    def _bind_rect_attributes(self, program: _Program) -> None:
        gl = self._gl
        stride = _RECT_FLOATS * ctypes.sizeof(ctypes.c_float)
        layout = (
            (b"a_pos", 2, 0),
            (b"a_local", 2, 8),
            (b"a_half", 2, 16),
            (b"a_radius", 1, 24),
            (b"a_ring", 1, 28),
            (b"a_color", 4, 32),
        )
        for name, size, offset in layout:
            location = program.attrib(name)
            if location < 0:
                continue
            gl.glEnableVertexAttribArray(location)
            gl.glVertexAttribPointer(location, size, _FLOAT, 0, stride, ctypes.c_void_p(offset))

    def draw_glyph(self, mask: Any, pen_x: float, baseline_y: float, color: Any) -> None:
        if mask.width == 0 or mask.height == 0:
            return
        # 混合顺序相关：字形之前排队的矩形先画掉
        self._flush_rects()
        uv = self._atlas_uv(mask)
        if uv is None:  # 图集满且无法重排（掩码比整张图还大）——直接跳过不崩
            return
        u0, v0, u1, v1 = uv
        ox, oy = self._origin
        # **对齐到整设备像素再画**：掩码是按整数 ppem 光栅化的，若落在小数
        # 坐标上，`GL_LINEAR` 会把每个字形重采样一遍——125% 屏上笔画忽粗忽细、
        # 小字发虚（正是"不如 Electron 清晰"的观感来源）。等比 DPI 下笔位几乎
        # 总是小数，所以这一步不是可选项。真正优雅的解是亚像素相位光栅化
        # （按 1/4 像素出多份掩码），那是后续优化；对齐是当下正确的那一档。
        left = float(round(pen_x + mask.left - ox))
        top = float(round(baseline_y + mask.top - oy))
        right = left + float(mask.width)
        bottom = top + float(mask.height)
        r, g, b, a = color.r / 255.0, color.g / 255.0, color.b / 255.0, float(color.a)
        # 顶点顺序与 uv 对应：左上、右上、右下 / 左上、右下、左下
        corners = (
            (left, top, u0, v1),
            (right, top, u1, v1),
            (right, bottom, u1, v0),
            (left, top, u0, v1),
            (right, bottom, u1, v0),
            (left, bottom, u0, v0),
        )
        for px, py, u, v in corners:
            self._glyph_vertices.extend((px, py, u, v, r, g, b, a))

    def _flush_glyphs(self) -> None:
        if not self._glyph_vertices:
            return
        gl = self._gl
        program = self._glyph
        array = (ctypes.c_float * len(self._glyph_vertices))(*self._glyph_vertices)
        gl.glUseProgram(program.id)
        vw, vh = self._viewport_size()
        gl.glUniform2f(program.uniform(b"u_viewport"), float(vw), float(vh))
        gl.glUniform1i(program.uniform(b"u_texture"), 0)
        gl.glActiveTexture(_TEXTURE0)
        gl.glBindTexture(_TEXTURE_2D, self._atlas_texture)
        gl.glBindBuffer(_ARRAY_BUFFER, self._vbo)
        gl.glBufferData(_ARRAY_BUFFER, ctypes.sizeof(array), array, _STREAM_DRAW)
        stride = _GLYPH_FLOATS * ctypes.sizeof(ctypes.c_float)
        for name, size, offset in ((b"a_pos", 2, 0), (b"a_uv", 2, 8), (b"a_color", 4, 16)):
            location = program.attrib(name)
            if location >= 0:
                gl.glEnableVertexAttribArray(location)
                gl.glVertexAttribPointer(location, size, _FLOAT, 0, stride, ctypes.c_void_p(offset))
        gl.glDrawArrays(_TRIANGLES, 0, len(self._glyph_vertices) // _GLYPH_FLOATS)
        gl.glBindBuffer(_ARRAY_BUFFER, 0)
        gl.glBindTexture(_TEXTURE_2D, 0)
        gl.glUseProgram(0)
        self._glyph_vertices.clear()

    def _ensure_atlas(self) -> None:
        if self._atlas_texture:
            return
        gl = self._gl
        texture = ctypes.c_uint(0)
        gl.glGenTextures(1, ctypes.byref(texture))
        gl.glBindTexture(_TEXTURE_2D, texture.value)
        gl.glPixelStorei(_UNPACK_ALIGNMENT, 1)
        gl.glTexImage2D(
            _TEXTURE_2D, 0, _RED, _ATLAS_SIZE, _ATLAS_SIZE, 0, _RED, _UNSIGNED_BYTE, None
        )
        gl.glTexParameteri(_TEXTURE_2D, _TEXTURE_MIN_FILTER, _LINEAR)
        gl.glTexParameteri(_TEXTURE_2D, _TEXTURE_MAG_FILTER, _LINEAR)
        gl.glTexParameteri(_TEXTURE_2D, _TEXTURE_WRAP_S, _CLAMP_TO_EDGE)
        gl.glTexParameteri(_TEXTURE_2D, _TEXTURE_WRAP_T, _CLAMP_TO_EDGE)
        gl.glBindTexture(_TEXTURE_2D, 0)
        self._atlas_texture = texture.value

    def _atlas_uv(self, mask: Any) -> tuple[float, float, float, float] | None:
        """把掩码放进图集，返回**归一化 uv**（v 的下界/上界对应掩码的下/上行）。

        打包用 shelf（行式）算法：确定、零碎片整理。图集满时**先冲掉当前批次**
        再清空重排——否则已排队的顶点还引用着旧 uv。
        """
        coverage = bytes(mask.coverage)
        key = (mask.width, mask.height, mask.left, mask.top, coverage)
        hit = self._atlas.get(key)
        if hit is not None:
            return hit
        w, h = mask.width, mask.height
        if w > _ATLAS_SIZE or h > _ATLAS_SIZE:
            return None
        self._ensure_atlas()
        if self._atlas_x + w > _ATLAS_SIZE:
            self._atlas_x = 0
            self._atlas_y += self._atlas_row_h + 1
            self._atlas_row_h = 0
        if self._atlas_y + h > _ATLAS_SIZE:
            self._flush_glyphs()
            self._atlas.clear()
            self._atlas_x = self._atlas_y = self._atlas_row_h = 0
        x, y = self._atlas_x, self._atlas_y
        # GL 纹理行序自下而上：把块放到 y_gl，并**反转行**上传，使 t 增大 = 掩码向下
        y_gl = _ATLAS_SIZE - (y + h)
        rows = [coverage[i * w : (i + 1) * w] for i in range(h)]
        rows.reverse()
        data = b"".join(rows)
        gl = self._gl
        gl.glBindTexture(_TEXTURE_2D, self._atlas_texture)
        gl.glTexSubImage2D(
            _TEXTURE_2D, 0, x, y_gl, w, h, _RED, _UNSIGNED_BYTE, ctypes.c_char_p(data)
        )
        gl.glBindTexture(_TEXTURE_2D, 0)
        uv = (
            x / _ATLAS_SIZE,
            y_gl / _ATLAS_SIZE,
            (x + w) / _ATLAS_SIZE,
            (y_gl + h) / _ATLAS_SIZE,
        )
        self._atlas[key] = uv
        self._atlas_x += w + 1
        self._atlas_row_h = max(self._atlas_row_h, h)
        return uv

    def create_texture(self, width: int, height: int, pixels: bytes) -> int:
        gl = self._gl
        texture = ctypes.c_uint(0)
        gl.glGenTextures(1, ctypes.byref(texture))
        gl.glBindTexture(_TEXTURE_2D, texture.value)
        gl.glPixelStorei(_UNPACK_ALIGNMENT, 1)
        gl.glTexImage2D(
            _TEXTURE_2D,
            0,
            _RGBA8,
            width,
            height,
            0,
            _RGBA,
            _UNSIGNED_BYTE,
            ctypes.c_char_p(pixels),
        )
        gl.glTexParameteri(_TEXTURE_2D, _TEXTURE_MIN_FILTER, _LINEAR)
        gl.glTexParameteri(_TEXTURE_2D, _TEXTURE_MAG_FILTER, _LINEAR)
        gl.glBindTexture(_TEXTURE_2D, 0)
        self._textures[texture.value] = texture.value
        return texture.value

    def destroy_texture(self, handle: int) -> None:
        if handle in self._textures:
            tex = ctypes.c_uint(handle)
            self._gl.glDeleteTextures(1, ctypes.byref(tex))
            del self._textures[handle]
        # 字形缓存里同 id 的条目下一帧会被重建，这里不主动清理（粒度小）

    def read_pixels(self) -> bytes:
        gl = self._gl
        size = self._width * self._height * 4
        buffer = ctypes.create_string_buffer(size)
        gl.glBindFramebuffer(_FRAMEBUFFER, self._fb)
        gl.glPixelStorei(_PACK_ALIGNMENT, 1)
        gl.glReadPixels(0, 0, self._width, self._height, _RGBA, _UNSIGNED_BYTE, buffer)
        data = buffer.raw
        # GL 行序自下而上，显示列表/FrameBuffer 约定自上而下 → 按行翻转
        stride = self._width * 4
        rows = [data[y * stride : (y + 1) * stride] for y in range(self._height)]
        rows.reverse()
        return b"".join(rows)

    # ------------------------------------------------------------ 内部

    def close(self) -> None:
        # 挂到别人的上下文时，删除 GL 资源前必须让那个上下文 current
        if self._make_current_cb is not None:
            self._make_current_cb()
        gl = self._gl
        for buffer_id in (self._vbo, self._rect_vbo):
            if buffer_id:
                buffer = ctypes.c_uint(buffer_id)
                gl.glDeleteBuffers(1, ctypes.byref(buffer))
        self._vbo = self._rect_vbo = 0
        if self._fb:
            fb = ctypes.c_uint(self._fb)
            gl.glDeleteFramebuffers(1, ctypes.byref(fb))
            self._fb = 0
        if self._color_texture:
            tex = ctypes.c_uint(self._color_texture)
            gl.glDeleteTextures(1, ctypes.byref(tex))
            self._color_texture = 0
        for key in list(self._layers):
            self._delete_layer(key)
        if self._atlas_texture:
            tex = ctypes.c_uint(self._atlas_texture)
            gl.glDeleteTextures(1, ctypes.byref(tex))
            self._atlas_texture = 0
            self._atlas.clear()
        for handle in list(self._textures):
            tex = ctypes.c_uint(handle)
            gl.glDeleteTextures(1, ctypes.byref(tex))
        self._textures.clear()
        if self._context:
            gl.opengl32.wglMakeCurrent(None, None)
            gl.opengl32.wglDeleteContext(ctypes.c_void_p(self._context))
            self._context = 0
        if self._hdc and self._hwnd:
            self._gl.user32.ReleaseDC(ctypes.c_void_p(self._hwnd), ctypes.c_void_p(self._hdc))
            self._gl.user32.DestroyWindow(ctypes.c_void_p(self._hwnd))
            self._hdc = self._hwnd = 0
        if self._detach_cb is not None:
            # 上下文归调用方（SDL）所有：只解除，不销毁
            self._detach_cb()
            self._detach_cb = None


def sdl_gl_driver(sdl_backend: Any, window_id: int) -> WglGLDriver:
    """把 GL 驱动挂到**SDL 创建的窗口上下文**上（R8.6 真窗口上屏）。

    窗口必须以 `WindowSpec(opengl=True)` 创建。上下文归 SDL 所有：这里只创建/
    绑定/换链，`close()` 时解除而不销毁（SDL 负责窗口生命周期）。

    为什么走 SDL 而不是自己建窗口：窗口、IME、剪贴板、DPI、跨平台事件
    都已经在 SDL2Backend 里；GL 只借它的上下文与换链，两件事各归其位。
    """
    lib = sdl_backend.library
    window = sdl_backend.native_window(window_id)
    context = lib.SDL_GL_CreateContext(window)
    if not context:
        raise GLUnavailableError("SDL_GL_CreateContext 失败（窗口是否用 opengl=True 创建？）")
    if lib.SDL_GL_MakeCurrent(window, context) != 0:
        lib.SDL_GL_DeleteContext(context)
        raise GLUnavailableError("SDL_GL_MakeCurrent 失败")

    def get_proc(name: bytes) -> int | None:
        address = lib.SDL_GL_GetProcAddress(name)
        return None if not address else int(address)

    def make_current() -> None:
        lib.SDL_GL_MakeCurrent(window, context)

    def present() -> None:
        lib.SDL_GL_SwapWindow(window)

    driver = WglGLDriver(attach=(_GL(get_proc=get_proc), present, make_current))

    def detach() -> None:
        lib.SDL_GL_MakeCurrent(window, None)
        lib.SDL_GL_DeleteContext(context)

    driver._detach_cb = detach
    return driver


def windows_gl_driver() -> WglGLDriver | None:
    """拿到一个可用的 WGL 驱动；不可用时返回 None（调用方决定回退）。

    返回 None 而不是抛错：GL 后端是**可选**的加速路径，软件光栅永远是兜底。
    让"这台机器没有 GL"变成一句可处理的返回值，而不是一个要 try/except 的异常。
    """
    try:
        return WglGLDriver()
    except GLUnavailableError:
        return None
