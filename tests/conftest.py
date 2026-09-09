"""测试公共装置。

原则：**时间、随机数、字体度量都必须是可注入的**。
只要渲染读的是注入时钟而不是墙上时钟，截图才能在三台机器上完全一致。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass
class FakeClock:
    """可控时钟：让"光标闪烁""动画进度"这类东西在测试里是确定的。"""

    now: float = 0.0

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def time(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def any_backend() -> str:
    """返回一个可用的后端名；CI 无显示环境时应当返回 headless。"""
    import os

    return os.environ.get("NANOUI_BACKEND", "headless")
