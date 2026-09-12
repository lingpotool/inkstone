"""ThemeScope —— 主题在组件树里的作用域载体（R6.1，docs/20）。

包一层 InheritedWidget，主题就能只覆盖一棵子树：

    侧栏暗色、主区亮色共存；改侧栏的主题，只标脏侧栏里读过 `context.theme`
    的那些组件，主区的 build 计数器纹丝不动。

没包 ThemeScope 的地方回落到 `BuildOwner.theme`（根部主题）——
所以 R1.10 的"换主题全树标脏"成为"根作用域变了"的特例，而不是另一套机制。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .key import Key
from .widget import InheritedWidget, Widget

if TYPE_CHECKING:  # 只作类型注解，避免 core → style 多一条运行期依赖
    from ..style import Theme

__all__ = ["ThemeScope"]


class ThemeScope(InheritedWidget):
    """把一份 Theme 挂到子树根部。后代通过 `context.theme` 读到它。"""

    def __init__(self, theme: Theme, child: Widget, *, key: Key | None = None) -> None:
        super().__init__(child, key=key)
        self.theme = theme

    def update_should_notify(self, old: InheritedWidget) -> bool:
        assert isinstance(old, ThemeScope)
        # 同一实例才不算变。Theme 是 frozen dataclass，`==` 要递归比 26 个字段，
        # 而"值相等的新实例"重建一次也无害——选便宜的判定。
        return old.theme is not self.theme
