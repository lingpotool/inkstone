"""组件树（Widget / Element / RenderObject）与帧调度的单元测试。

对应 docs/06 §8 的验收项：

- 同一帧 100 次 setState 只触发一次重建
- 列表重排，Key 正确的状态零丢失
- 阶段外改状态抛出带节点路径的错误

测试里定义的 Box / Column 不是产品代码，是用来把这套机制跑起来的最小脚手架——
真正的最小组件集（ROADMAP Phase 1 item 7）会基于同样的接口实现。
"""

import pytest

from inkstone.core import (
    BuildOwner,
    FrameError,
    FramePhase,
    Key,
    LeafRenderObjectElement,
    MultiChildRenderObjectElement,
    RenderObject,
    RenderObjectWidget,
    State,
    StatefulWidget,
    StatelessWidget,
    ValueKey,
    Widget,
)
from inkstone.layout import BoxConstraints, RenderColumn, Size

# ---------------------------------------------------------------- 脚手架


class BoxRenderObject(RenderObject):
    def __init__(self, width: float = 10.0, height: float = 10.0, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.width = width
        self.height = height
        self.paint_count = 0

    def perform_layout(self, constraints: BoxConstraints) -> Size:
        return constraints.constrain(Size(self.width, self.height))

    def paint(self, context: object) -> None:  # type: ignore[override]
        self.paint_count += 1


class Box(RenderObjectWidget):
    def __init__(
        self, width: float = 10.0, height: float = 10.0, *, key: Key | None = None
    ) -> None:
        super().__init__(key=key)
        self.width = width
        self.height = height

    def create_render_object(self) -> BoxRenderObject:
        return BoxRenderObject(self.width, self.height)

    def update_render_object(self, render_object: RenderObject) -> None:
        assert isinstance(render_object, BoxRenderObject)
        render_object.width = self.width
        render_object.height = self.height

    def create_element(self) -> LeafRenderObjectElement:
        return LeafRenderObjectElement(self)


class ColumnElement(MultiChildRenderObjectElement):
    def child_widgets(self) -> tuple[Widget, ...]:
        widget = self.widget
        assert isinstance(widget, Column)
        return widget.children

    def insert_child_render_object(self, child: RenderObject, slot: object | None) -> None:
        container = self.render_object
        assert isinstance(container, RenderColumn)
        container.add(child)

    def remove_child_render_object(self, child: RenderObject) -> None:
        """幂等：不在容器里的子级静默返回。"""
        container = self.render_object
        if not isinstance(container, RenderColumn):
            return
        if any(c is child for c in container.children):
            container.remove(child)


class Column(RenderObjectWidget):
    def __init__(self, children: tuple[Widget, ...] = (), *, key: Key | None = None) -> None:
        super().__init__(key=key)
        self.children = children

    def create_render_object(self) -> RenderColumn:
        return RenderColumn()

    def create_element(self) -> ColumnElement:
        return ColumnElement(self)


class Counter(StatefulWidget):
    def __init__(self, label: str = "", *, key: Key | None = None) -> None:
        super().__init__(key=key)
        self.label = label

    def create_state(self) -> "CounterState":
        return CounterState()


class CounterState(State["Counter"]):
    def init_state(self) -> None:
        self.count = 0
        self.init_calls = 1
        self.dispose_calls = 0

    def build(self, context: object) -> Widget:  # type: ignore[override]
        return Box(width=self.count + 1, height=10)

    def dispose(self) -> None:
        self.dispose_calls += 1


class Greeting(StatelessWidget):
    def __init__(self, name: str, *, key: Key | None = None) -> None:
        super().__init__(key=key)
        self.name = name

    def build(self, context: object) -> Widget:  # type: ignore[override]
        return Box(width=len(self.name), height=5)


class _FakeContext:
    """最小的 PaintContext 实现——gfx 落地前用它把 paint 阶段跑通。"""


CONSTRAINTS = BoxConstraints(max_width=400, max_height=400)


# ---------------------------------------------------------------- 测试


class TestWidgetIdentity:
    def test_same_type_and_no_key_can_update(self):
        assert Widget.can_update(Box(1), Box(2))

    def test_same_type_and_same_key_can_update(self):
        assert Widget.can_update(Box(1, key=ValueKey("a")), Box(2, key=ValueKey("a")))

    def test_different_key_cannot_update(self):
        assert not Widget.can_update(Box(1, key=ValueKey("a")), Box(2, key=ValueKey("b")))

    def test_different_type_cannot_update(self):
        assert not Widget.can_update(Box(1), Column())

    def test_key_equality_is_by_value(self):
        assert ValueKey("a") == ValueKey("a")
        assert ValueKey("a") != ValueKey("b")


class TestMounting:
    def test_mount_creates_all_three_trees(self):
        owner = BuildOwner()
        element = owner.mount(Column((Box(20, 10), Box(30, 10))))

        assert element.widget is not None
        assert len(element.children) == 2
        root = owner.root_render_object
        assert isinstance(root, RenderColumn)
        assert len(root.children) == 2

    def test_layout_runs_through_the_render_tree(self):
        owner = BuildOwner()
        owner.mount(Column((Box(20, 10), Box(30, 10))))
        size = owner.begin_frame(CONSTRAINTS)
        assert size is not None
        assert size.width == pytest.approx(30.0)
        assert size.height == pytest.approx(20.0)

    def test_mounting_twice_is_rejected(self):
        owner = BuildOwner()
        owner.mount(Box())
        with pytest.raises(FrameError):
            owner.mount(Box())

    def test_stateless_element_builds_subtree(self):
        owner = BuildOwner()
        owner.mount(Greeting("hello"))
        owner.begin_frame(CONSTRAINTS)
        root = owner.root_render_object
        assert isinstance(root, BoxRenderObject)
        assert root.width == pytest.approx(5.0)


class TestStateLifecycle:
    def test_init_state_runs_before_first_build(self):
        owner = BuildOwner()
        element = owner.mount(Counter())
        owner.begin_frame(CONSTRAINTS)
        state = element.state  # type: ignore[attr-defined]
        assert state.init_calls == 1
        assert state.mounted

    def test_set_state_triggers_rebuild_next_frame(self):
        owner = BuildOwner()
        element = owner.mount(Counter())
        owner.begin_frame(CONSTRAINTS)
        state = element.state  # type: ignore[attr-defined]

        state.count = 42
        state.set_state()
        assert owner.dirty_count == 1

        owner.begin_frame(CONSTRAINTS)
        root = owner.root_render_object
        assert isinstance(root, BoxRenderObject)
        assert root.width == pytest.approx(43.0)

    def test_set_state_with_callback_is_atomic(self):
        owner = BuildOwner()
        element = owner.mount(Counter())
        owner.begin_frame(CONSTRAINTS)
        state = element.state  # type: ignore[attr-defined]

        state.set_state(lambda: setattr(state, "count", 7))
        owner.begin_frame(CONSTRAINTS)
        assert state.count == 7

    def test_dispose_runs_on_unmount(self):
        owner = BuildOwner()
        element = owner.mount(Counter())
        owner.begin_frame(CONSTRAINTS)
        state = element.state  # type: ignore[attr-defined]

        element.unmount()
        assert state.dispose_calls == 1
        assert not state.mounted

    def test_set_state_after_unmount_raises(self):
        owner = BuildOwner()
        element = owner.mount(Counter())
        owner.begin_frame(CONSTRAINTS)
        element.unmount()
        with pytest.raises(RuntimeError):
            element.state.set_state()  # type: ignore[attr-defined]


class TestBatchProcessing:
    def test_one_hundred_set_states_rebuild_once(self):
        owner = BuildOwner()
        element = owner.mount(Counter())
        owner.begin_frame(CONSTRAINTS)
        state = element.state  # type: ignore[attr-defined]

        owner.build_count = 0
        for i in range(100):
            state.set_state(lambda i=i: setattr(state, "count", i))

        assert owner.dirty_count == 1, "100 次 setState 必须只入队一次"
        owner.begin_frame(CONSTRAINTS)
        assert owner.build_count == 1, "一帧内必须只重建一次"

    def test_unconditional_set_state_in_build_is_caught(self):
        """build 里无条件 setState 会无限循环——必须有兜底并报清楚。"""

        class Spinner(StatefulWidget):
            def create_state(self) -> "SpinnerState":
                return SpinnerState()

        class SpinnerState(State):
            def init_state(self) -> None:
                self.n = 0

            def build(self, context: object) -> Widget:  # type: ignore[override]
                self.n += 1
                self.set_state()
                return Box(width=1, height=1)

        owner = BuildOwner()
        owner.mount(Spinner())
        with pytest.raises(FrameError) as exc:
            owner.begin_frame(CONSTRAINTS)
        assert "未收敛" in str(exc.value)


class TestFramePhaseGuard:
    def test_set_state_during_layout_raises(self):
        owner = BuildOwner()
        element = owner.mount(Counter())
        owner.begin_frame(CONSTRAINTS)
        state = element.state  # type: ignore[attr-defined]

        # 在 layout 阶段偷偷改状态：本该报错
        root = owner.root_render_object
        assert root is not None
        original = root.layout

        def naughty(constraints: BoxConstraints) -> Size:
            state.set_state()
            return original(constraints)

        root.layout = naughty  # type: ignore[method-assign]
        with pytest.raises(FrameError) as exc:
            owner.begin_frame(CONSTRAINTS)

        message = str(exc.value)
        assert "layout" in message
        assert "路径" in message
        assert "建议" in message

    def test_phase_returns_to_idle_after_frame(self):
        owner = BuildOwner()
        owner.mount(Box())
        owner.begin_frame(CONSTRAINTS)
        assert owner.phase is FramePhase.IDLE


class TestKeyPreservesIdentity:
    def _counts(self, owner: BuildOwner) -> list[int]:
        root = owner.root_render_object
        assert isinstance(root, RenderColumn)
        return [c.width - 1 for c in root.children if isinstance(c, BoxRenderObject)]

    def test_reorder_with_keys_keeps_state(self):
        owner = BuildOwner()
        element = owner.mount(
            Column((Counter("a", key=ValueKey("a")), Counter("b", key=ValueKey("b"))))
        )
        owner.begin_frame(CONSTRAINTS)

        states = [child.state for child in element.children]  # type: ignore[attr-defined]
        states[0].count = 10
        states[1].count = 20
        for s in states:
            s.set_state()
        owner.begin_frame(CONSTRAINTS)

        # 把两个子级顺序颠倒
        element.update(Column((Counter("b", key=ValueKey("b")), Counter("a", key=ValueKey("a")))))
        owner.begin_frame(CONSTRAINTS)

        # Key 正确的节点状态跟着节点走，不是留在槽位上
        by_label = {}
        for child in element.children:  # type: ignore[attr-defined]
            widget = child.widget
            assert isinstance(widget, Counter)
            by_label[widget.label] = child.state.count  # type: ignore[attr-defined]
        assert by_label == {"a": 10, "b": 20}

    def test_reorder_without_keys_loses_state(self):
        """反面教材：不给 Key 时状态跟着槽位走，这就是"必须给稳定 Key"的原因。"""
        owner = BuildOwner()
        element = owner.mount(Column((Counter("a"), Counter("b"))))
        owner.begin_frame(CONSTRAINTS)

        states = [child.state for child in element.children]  # type: ignore[attr-defined]
        states[0].count = 10
        states[1].count = 20
        for s in states:
            s.set_state()
        owner.begin_frame(CONSTRAINTS)

        element.update(Column((Counter("b"), Counter("a"))))
        owner.begin_frame(CONSTRAINTS)

        # 按下标复用：0 号槽位仍是原来的 Element，于是 count 没跟着 label 走
        counts = [child.state.count for child in element.children]  # type: ignore[attr-defined]
        assert counts == [10, 20]

    def test_changing_key_rebuilds_the_subtree(self):
        owner = BuildOwner()
        element = owner.mount(Column((Counter("a", key=ValueKey("a")),)))
        owner.begin_frame(CONSTRAINTS)
        first_state = element.children[0].state  # type: ignore[attr-defined]

        element.update(Column((Counter("a", key=ValueKey("z")),)))
        owner.begin_frame(CONSTRAINTS)

        new_state = element.children[0].state  # type: ignore[attr-defined]
        assert new_state is not first_state
        assert first_state.dispose_calls == 1


class TestPaintDirtyTracking:
    def test_clean_subtree_is_skipped(self):
        owner = BuildOwner()
        owner.mount(Column((Box(10, 10), Box(10, 10))))
        owner.begin_frame(CONSTRAINTS, _FakeContext())

        root = owner.root_render_object
        assert isinstance(root, RenderColumn)
        first = root.children[0]
        assert isinstance(first, BoxRenderObject)
        assert first.paint_count == 1

        # 第二次绘制：整棵树都干净，一次都不会再画
        owner.begin_frame(CONSTRAINTS, _FakeContext())
        assert first.paint_count == 1

    def test_mark_needs_paint_bubbles_to_root(self):
        owner = BuildOwner()
        owner.mount(Column((Box(10, 10),)))
        owner.begin_frame(CONSTRAINTS, _FakeContext())

        root = owner.root_render_object
        assert isinstance(root, RenderColumn)
        assert not root.needs_paint

        leaf = root.children[0]
        assert isinstance(leaf, BoxRenderObject)
        leaf.mark_needs_paint()
        assert root.needs_paint, "子级脏了必须冒泡到根，否则根会跳过整棵子树"

        owner.begin_frame(CONSTRAINTS, _FakeContext())
        assert leaf.paint_count == 2


class TestDebugPath:
    def test_path_shows_widget_types(self):
        owner = BuildOwner()
        element = owner.mount(Column((Box(), Box())))
        owner.begin_frame(CONSTRAINTS)
        assert element.describe_path() == "Column"
        assert element.children[0].describe_path() == "Column > Box[0]"
