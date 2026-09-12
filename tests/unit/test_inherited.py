"""Inherited 环境传播（R6.1，docs/20）的单元测试。

验收项：

- 子树覆盖：侧栏暗色 + 主区亮色共存
- 定向标脏：改侧栏主题，主区 build 计数器不动
- 移除 InheritedWidget 后，依赖者正确回退到上一级环境
- 根主题（BuildOwner.theme）变化仍是全树标脏——"根作用域变了"的特例
"""

import pytest

from inkstone.core import (
    BuildOwner,
    Element,
    Key,
    LeafRenderObjectElement,
    MultiChildRenderObjectElement,
    RenderObject,
    RenderObjectWidget,
    StatelessWidget,
    ThemeScope,
    Widget,
)
from inkstone.layout import BoxConstraints, RenderBox, RenderColumn, Size
from inkstone.style import Theme

# ---------------------------------------------------------------- 脚手架
# 与 test_core_trees.py 同构的最小脚手架：一个叶子 Box + 一个竖排 Column。


class BoxRenderObject(RenderObject):
    def perform_layout(self, constraints: BoxConstraints) -> Size:
        return constraints.constrain(Size(10.0, 10.0))

    def paint(self, context: object) -> None:  # type: ignore[override]
        pass


class Box(RenderObjectWidget):
    def create_render_object(self) -> BoxRenderObject:
        return BoxRenderObject()

    def create_element(self) -> LeafRenderObjectElement:
        return LeafRenderObjectElement(self)


class ColumnElement(MultiChildRenderObjectElement):
    def child_widgets(self) -> tuple[Widget, ...]:
        widget = self.widget
        assert isinstance(widget, Column)
        return widget.children

    def insert_child_render_object(self, child: RenderBox, slot: object | None) -> None:
        container = self.render_object
        assert isinstance(container, RenderColumn)
        container.add(child)

    def remove_child_render_object(self, child: RenderBox) -> None:
        container = self.render_object
        if isinstance(container, RenderColumn) and any(c is child for c in container.children):
            container.remove(child)


class Column(RenderObjectWidget):
    def __init__(self, children: tuple[Widget, ...], *, key: Key | None = None) -> None:
        super().__init__(key=key)
        self.children = children

    def create_render_object(self) -> RenderColumn:
        return RenderColumn()

    def create_element(self) -> ColumnElement:
        return ColumnElement(self)


class ThemeProbe(StatelessWidget):
    """build 时读 `context.theme` 并把主题名记进日志——依赖登记的探针。"""

    def __init__(self, log: list[str]) -> None:
        super().__init__()
        self.log = log

    def build(self, context: Element) -> Widget:
        self.log.append(context.theme.name)
        return Box()


CONSTRAINTS = BoxConstraints(max_width=400, max_height=400)

# ---------------------------------------------------------------- 测试


class TestSubtreeOverride:
    def test_sidebar_dark_main_light_coexist(self):
        dark_log: list[str] = []
        light_log: list[str] = []
        owner = BuildOwner(theme=Theme.light())
        owner.mount(
            Column(
                (
                    ThemeScope(Theme.dark(), ThemeProbe(dark_log)),
                    ThemeProbe(light_log),
                )
            )
        )
        owner.begin_frame(CONSTRAINTS)

        assert dark_log == ["dark"], "ThemeScope 下的后代必须读到暗色主题"
        assert light_log == ["light"], "作用域外的主区必须仍读根主题"

    def test_nested_scopes_nearest_wins(self):
        outer_log: list[str] = []
        inner_log: list[str] = []
        dark = Theme.dark()
        custom = Theme.light(name="brand")
        owner = BuildOwner(theme=Theme.light())
        owner.mount(
            ThemeScope(
                dark,
                Column(
                    (
                        ThemeProbe(outer_log),
                        ThemeScope(custom, ThemeProbe(inner_log)),
                    )
                ),
            )
        )
        owner.begin_frame(CONSTRAINTS)

        assert outer_log == ["dark"]
        assert inner_log == ["brand"], "嵌套作用域必须就近取值"


class TestTargetedDirty:
    """改侧栏主题，主区 build 计数器不动（docs/20 的核心验收）。"""

    def test_scope_update_dirties_only_its_dependents(self):
        sidebar_log: list[str] = []
        main_log: list[str] = []
        sidebar_widget = ThemeProbe(sidebar_log)  # 同一实例：堵住"级联重建"这条通道
        owner = BuildOwner(theme=Theme.light())
        root = owner.mount(Column((ThemeScope(Theme.dark(), sidebar_widget), ThemeProbe(main_log))))
        owner.begin_frame(CONSTRAINTS)
        sidebar_log.clear()
        main_log.clear()

        scope_element = root.children[0]
        scope_element.update(ThemeScope(Theme.dark(name="dark2"), sidebar_widget))
        owner.begin_frame(CONSTRAINTS)

        assert sidebar_log == ["dark2"], "依赖者必须被定向标脏并重建"
        assert main_log == [], "主区与侧栏作用域无关，一次都不许重建"

    def test_same_theme_instance_does_not_notify(self):
        log: list[str] = []
        dark = Theme.dark()
        child = ThemeProbe(log)
        owner = BuildOwner(theme=Theme.light())
        root = owner.mount(Column((ThemeScope(dark, child),)))
        owner.begin_frame(CONSTRAINTS)
        log.clear()

        scope_element = root.children[0]
        scope_element.update(ThemeScope(dark, child))  # 主题实例没变
        owner.begin_frame(CONSTRAINTS)

        assert log == [], "update_should_notify 为 False 时不许标脏"


class TestRemovalFallback:
    def test_removing_scope_falls_back_to_root_theme(self):
        log: list[str] = []
        owner = BuildOwner(theme=Theme.light())
        root = owner.mount(Column((ThemeScope(Theme.dark(), ThemeProbe(log)),)))
        owner.begin_frame(CONSTRAINTS)
        assert log == ["dark"]
        scope_element = root.children[0]

        root.update(Column((ThemeProbe(log),)))
        owner.begin_frame(CONSTRAINTS)

        assert log[-1] == "light", "作用域被移除后必须回退到根主题"
        assert not scope_element._dependents, "卸载必须解除依赖登记，不许留悬挂引用"


class TestRootThemeIsTheImplicitRootScope:
    def test_root_theme_change_still_rebuilds_everything(self):
        """R1.10 的全树标脏保留为"根作用域变了"的特例。"""
        first_log: list[str] = []
        second_log: list[str] = []
        owner = BuildOwner(theme=Theme.light())
        owner.mount(Column((ThemeProbe(first_log), ThemeProbe(second_log))))
        owner.begin_frame(CONSTRAINTS)
        first_log.clear()
        second_log.clear()

        owner.theme = Theme.dark()
        # 全树标脏：Column + 2 个 Probe + 2 个 Box = 5 个节点
        assert owner.dirty_count == 5
        owner.begin_frame(CONSTRAINTS)

        assert first_log == ["dark"]
        assert second_log == ["dark"]


class TestInheritedErrors:
    def test_update_should_notify_is_mandatory(self):
        from inkstone.core import InheritedWidget

        class Bare(InheritedWidget):
            pass

        owner = BuildOwner(theme=Theme.light())
        root = owner.mount(Column((Bare(Box()),)))
        owner.begin_frame(CONSTRAINTS)
        scope_element = root.children[0]

        with pytest.raises(NotImplementedError, match="update_should_notify"):
            scope_element.update(Bare(Box()))
