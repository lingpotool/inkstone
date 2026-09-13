"""SDL2 二进制的获取策略（R8.5）。

**不在仓库里塞二进制**，而是三层声明式策略：环境变量覆盖 > 可选依赖
`inkstone[sdl2]`（pysdl2-dll 提供三平台预编译库）> 系统已装 SDL2。
这里的测试用注入的假加载器验证**优先级与报错指引**，不需要真装 SDL2；
最后一条是真机冒烟，装不到库时明确 skip（CI 上就是这种情形）。
"""

from __future__ import annotations

import ctypes
import ctypes.util

import pytest

from inkstone.backend import sdl2 as sdl2_mod
from inkstone.backend.base import BackendError, Cursor, ImeRect, WindowSpec
from inkstone.backend.sdl2 import SDL2Backend, load_sdl2


class _FakeLib:
    """够用的假库：只记录它是谁，不提供任何 SDL 函数。"""

    def __init__(self, name: str) -> None:
        self.name = name


class TestLoaderPrecedence:
    def test_env_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("INKSTONE_SDL2", "D:/custom/SDL2.dll")
        calls: list[str] = []

        def fake_cdll(name: str) -> _FakeLib:
            calls.append(name)
            return _FakeLib(name)

        monkeypatch.setattr(ctypes, "CDLL", fake_cdll)
        lib = load_sdl2()
        assert isinstance(lib, _FakeLib)
        assert lib.name == "D:/custom/SDL2.dll"
        assert calls == ["D:/custom/SDL2.dll"], "覆盖生效后不该再试其它来源"

    def test_package_source_beats_system(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("INKSTONE_SDL2", raising=False)
        sentinel = _FakeLib("from-pysdl2")
        monkeypatch.setattr(sdl2_mod, "_load_from_pysdl2", lambda errors: sentinel)

        def boom(name: str) -> _FakeLib:
            raise AssertionError(f"不该走到系统库查找：{name}")

        monkeypatch.setattr(ctypes, "CDLL", boom)
        assert load_sdl2() is sentinel

    def test_failure_message_lists_every_remedy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("INKSTONE_SDL2", raising=False)
        monkeypatch.setattr(sdl2_mod, "_load_from_pysdl2", lambda errors: None)
        monkeypatch.setattr(ctypes.util, "find_library", lambda name: None)

        def boom(name: str) -> _FakeLib:
            raise OSError(f"not found: {name}")

        monkeypatch.setattr(ctypes, "CDLL", boom)
        with pytest.raises(BackendError) as exc:
            load_sdl2()
        message = str(exc.value)
        assert "inkstone[sdl2]" in message
        assert "HeadlessBackend" in message
        assert "INKSTONE_SDL2" in message

    def test_env_override_failure_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """覆盖路径无效时不该直接死，继续按优先级往下找。"""
        monkeypatch.setenv("INKSTONE_SDL2", "D:/missing/SDL2.dll")
        sentinel = _FakeLib("from-pysdl2")
        monkeypatch.setattr(sdl2_mod, "_load_from_pysdl2", lambda errors: sentinel)

        def fake_cdll(name: str) -> _FakeLib:
            if name == "D:/missing/SDL2.dll":
                raise OSError("not found")
            return _FakeLib(name)

        monkeypatch.setattr(ctypes, "CDLL", fake_cdll)
        assert load_sdl2() is sentinel


class TestRealSDL2:
    """真机冒烟：装不到 SDL2 时 skip（CI 就是这种情形），不假装测过。"""

    @pytest.fixture
    def backend(self):
        try:
            load_sdl2()
        except BackendError:
            pytest.skip("本机没有 SDL2（未装 inkstone[sdl2] 或系统库）")
        made = SDL2Backend()
        try:
            made.initialize()
        except BackendError as error:
            pytest.skip(f"SDL2 初始化失败（可能是无显示环境）：{error}")
        yield made
        made.shutdown()

    def test_window_lifecycle_and_capabilities(self, backend: SDL2Backend) -> None:
        window = backend.create_window(WindowSpec(title="inkstone test", width=160, height=120))
        assert window > 0
        assert backend.dpi_scale(window) > 0.0
        backend.set_cursor(window, Cursor.TEXT)
        backend.start_text_input(window)
        backend.set_ime_rect(window, ImeRect(8.0, 8.0, 0.0, 16.0))
        backend.clipboard_set_text("inkstone")
        assert backend.clipboard_get_text() == "inkstone"
        backend.pump_events()  # 真窗口的事件泵不该崩
        backend.destroy_window(window)
