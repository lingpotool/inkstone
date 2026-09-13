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
    MAX_BUILD_FAILURES_PER_FRAME,
    BuildError,
    BuildOwner,
    Element,
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
from inkstone.gfx import DisplayList, DisplayListRecorder, resolve_state_ops
from inkstone.gfx.color import Color
from inkstone.layout import (
    BoxConstraints,
    RenderBox,
    RenderColumn,
    RenderRow,
    RenderScroll,
    Size,
)
from inkstone.layout.types import Rect
from inkstone.widgets import Flexible, Row

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
        # 断言 `size` 而不是 `width` 字段：字段是配置，`size` 才是**布局产物**。
        # 只断言字段的话，"改了属性但布局没重跑"这个 bug 会被完美遮蔽
        # （字段变了、几何没变，界面上什么都没发生）。
        assert root.size.width == pytest.approx(43.0), "属性更新必须触发重新布局"

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


class TestWidgetUpdateRelayouts:
    """`update_render_object` 之后必须标 layout 脏。

    审查实测：宽度属性 10 → 200，两帧后 `size.width` 仍是 10——
    因为 `RenderObjectElement.update` 只调了 `mark_needs_paint()`。
    只标绘制的话，改几何属性会**静默失效**：不报错、不崩溃，界面就是不动。
    """

    def test_child_size_change_relayouts_the_parent(self):
        owner = BuildOwner()
        element = owner.mount(Column((Box(20, 10), Box(30, 10))))
        owner.begin_frame(CONSTRAINTS)
        root = owner.root_render_object
        assert isinstance(root, RenderColumn)
        assert root.size.height == pytest.approx(20.0)

        element.update(Column((Box(20, 10), Box(30, 60))))
        owner.begin_frame(CONSTRAINTS)

        child = root.children[1]
        assert isinstance(child, BoxRenderObject)
        assert child.size.height == pytest.approx(60.0), "子级几何必须跟着属性更新"
        assert root.size.height == pytest.approx(70.0), "父容器也必须重排"

    def test_width_change_is_not_silently_dropped(self):
        """审查里那条实测用例：10 → 200 之后必须真的变宽。"""
        owner = BuildOwner()
        element = owner.mount(Box(10, 10))
        owner.begin_frame(CONSTRAINTS)
        root = owner.root_render_object
        assert isinstance(root, BoxRenderObject)
        assert root.size.width == pytest.approx(10.0)

        element.update(Box(200, 10))
        owner.begin_frame(CONSTRAINTS)
        assert root.size.width == pytest.approx(200.0)


class TestSlotPropagationOnReuse:
    """复用子级时，槽位（flex 权重）必须跟着更新。

    审查实测：`Row([Flexible(flex=1), Flexible(flex=2)])` 改成 `flex=[3,1]`，
    两帧后宽度仍是 33/67——因为复用路径调 `update(widget)` 时**没传 slot**，
    而且"顺序没变"也就不触发重挂，渲染树上的旧权重永远留档。
    """

    @staticmethod
    def _row(flexes: tuple[int, ...]) -> Row:
        return Row(
            children=tuple(Flexible(Box(height=10.0), flex=f) for f in flexes),
        )

    def test_flex_weight_change_is_not_silently_dropped(self):
        owner = BuildOwner()
        element = owner.mount(self._row((1, 2)))
        owner.begin_frame(BoxConstraints(max_width=100, max_height=100))
        root = owner.root_render_object
        assert isinstance(root, RenderRow)
        assert [c.size.width for c in root.children] == pytest.approx([100 / 3, 200 / 3])

        element.update(self._row((3, 1)))
        owner.begin_frame(BoxConstraints(max_width=100, max_height=100))

        assert [c.size.width for c in root.children] == pytest.approx([75.0, 25.0])
        assert [item.flex for item in root.items] == [3, 1], "渲染树上的权重也要更新"

    def test_element_slot_is_updated_on_reuse(self):
        owner = BuildOwner()
        element = owner.mount(self._row((1, 2)))
        owner.begin_frame(BoxConstraints(max_width=100, max_height=100))
        assert [child.slot for child in element.children] == [1, 2]

        element.update(self._row((5, 7)))
        owner.begin_frame(BoxConstraints(max_width=100, max_height=100))
        assert [child.slot for child in element.children] == [5, 7], "Element 上的槽位必须更新"

    def test_unchanged_slots_do_not_trigger_a_remount(self):
        """权重没变就不该白白重挂——复用路径要保持廉价。"""
        owner = BuildOwner()
        element = owner.mount(self._row((1, 2)))
        owner.begin_frame(BoxConstraints(max_width=100, max_height=100))
        root = owner.root_render_object
        assert isinstance(root, RenderRow)
        first_child = root.children[0]

        element.update(self._row((1, 2)))
        owner.begin_frame(BoxConstraints(max_width=100, max_height=100))
        assert root.children[0] is first_child, "权重没变时不该重建 RenderBox"


class FlakyCounter(StatefulWidget):
    """build 会按需抛异常的组件——用来验证 build 错误边界。"""

    def create_state(self) -> "FlakyCounterState":
        return FlakyCounterState()


class FlakyCounterState(State["FlakyCounter"]):
    def init_state(self) -> None:
        self.fail_next = 0
        self.builds = 0

    def build(self, context: object) -> Widget:  # type: ignore[override]
        self.builds += 1
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RuntimeError("boom: 故意炸")
        return Box(width=float(self.builds), height=10)


class TestBuildErrorBoundary:
    """一个子树 build 抛异常，不许把整批重建拖下水。

    修复前的行为：`rebuild()` 先清 `_dirty`、`flush_build` 先清空整批，
    异常一冒泡，该节点就停在"widget 已换、子树没更新"的不一致态，
    后续节点全部跳过，下一帧不重试，而且没有任何错误可见。
    """

    @staticmethod
    def _mount(owner: BuildOwner) -> tuple[Element, list[Element]]:
        element = owner.mount(Column((Counter("a"), FlakyCounter(), Counter("c"))))
        owner.begin_frame(CONSTRAINTS)
        return element, list(element.children)

    def test_sibling_subtrees_finish_this_frame(self) -> None:
        owner = BuildOwner()
        _, children = self._mount(owner)
        healthy = children[0].state  # type: ignore[attr-defined]
        flaky = children[1].state  # type: ignore[attr-defined]
        flaky.fail_next = 1

        healthy.count = 5
        healthy.set_state()
        flaky.set_state()

        with pytest.raises(BuildError) as exc:
            owner.begin_frame(CONSTRAINTS)

        assert "boom" in str(exc.value)
        assert "FlakyCounter" in str(exc.value), "错误里必须带节点路径"
        assert owner.errors, "错误必须记录到 owner.errors"
        assert "FlakyCounter" in owner.errors[0].path
        # 健康兄弟照常重建完成（修复前它会被整批跳过）
        assert healthy.count == 5
        root = owner.root_render_object
        assert isinstance(root, RenderColumn)
        healthy_ro = root.children[0]
        assert isinstance(healthy_ro, BoxRenderObject)
        assert healthy_ro.width == pytest.approx(6.0), "健康兄弟的新配置必须已经写进渲染对象"

        # 下一帧（坏节点已恢复）布局照常完成——树没有停在半路
        owner.begin_frame(CONSTRAINTS)
        assert healthy_ro.size.width == pytest.approx(6.0)

    def test_failed_node_stays_dirty_and_retries_next_frame(self) -> None:
        owner = BuildOwner()
        _, children = self._mount(owner)
        flaky = children[1].state  # type: ignore[attr-defined]
        flaky.builds = 0
        flaky.fail_next = 1
        flaky.set_state()

        with pytest.raises(BuildError):
            owner.begin_frame(CONSTRAINTS)

        # 本帧内重试一次就成功了（fail_next 只设了 1）
        assert flaky.builds == 2
        assert not children[1].dirty, "成功之后不该还是脏的"

        # 再炸一次，这回检查"保持脏"
        flaky.fail_next = 99
        flaky.set_state()
        with pytest.raises(BuildError):
            owner.begin_frame(CONSTRAINTS)
        assert children[1].dirty, "失败节点必须保持脏，否则下一帧永远不会重试"

        # 下一帧修好 → 自动重试成功，错误列表清空
        flaky.fail_next = 0
        owner.begin_frame(CONSTRAINTS)
        assert not children[1].dirty
        assert owner.errors == []

    def test_persistent_failure_gives_up_after_three_tries_in_the_frame(self) -> None:
        owner = BuildOwner()
        _, children = self._mount(owner)
        flaky = children[1].state  # type: ignore[attr-defined]
        flaky.builds = 0
        flaky.fail_next = 99
        flaky.set_state()

        with pytest.raises(BuildError) as exc:
            owner.begin_frame(CONSTRAINTS)

        assert flaky.builds == MAX_BUILD_FAILURES_PER_FRAME, (
            "本帧最多试 3 次——不设上限的话'每次 build 都抛'会把一帧拖成死循环"
        )
        assert len(owner.errors) == MAX_BUILD_FAILURES_PER_FRAME
        assert len(exc.value.failures) == MAX_BUILD_FAILURES_PER_FRAME
        assert children[1].dirty

    def test_errors_are_cleared_between_frames(self) -> None:
        owner = BuildOwner()
        _, children = self._mount(owner)
        flaky = children[1].state  # type: ignore[attr-defined]
        flaky.fail_next = 1
        flaky.set_state()
        with pytest.raises(BuildError):
            owner.begin_frame(CONSTRAINTS)
        assert owner.errors

        flaky.fail_next = 0
        owner.begin_frame(CONSTRAINTS)
        assert owner.errors == [], "错误列表属于「本帧」，不该跨帧堆积"


class TestFrameScheduling:
    """`on_frame_scheduled` —— set_state → 屏幕刷新这条闭环的出口。

    此前 `BuildOwner` 只有"入队"，没有任何"该画下一帧了"的通知，
    app 层 / 后端 vsync / motion 想接都接不上。
    """

    @staticmethod
    def _counter(owner: BuildOwner) -> CounterState:
        element = owner.mount(Counter())
        owner.begin_frame(CONSTRAINTS)
        return element.state  # type: ignore[attr-defined]

    def test_set_state_notifies_once(self) -> None:
        owner = BuildOwner()
        state = self._counter(owner)
        calls: list[int] = []
        owner.on_frame_scheduled = lambda: calls.append(1)

        state.set_state()
        assert len(calls) == 1

    def test_one_hundred_set_states_still_notify_once(self) -> None:
        owner = BuildOwner()
        state = self._counter(owner)
        calls: list[int] = []
        owner.on_frame_scheduled = lambda: calls.append(1)

        for i in range(100):
            state.set_state(lambda i=i: setattr(state, "count", i))
        assert len(calls) == 1, "同一帧里重复标脏只通知一次"

    def test_second_element_in_the_same_frame_does_not_notify_again(self) -> None:
        owner = BuildOwner()
        element = owner.mount(Column((Counter("a"), Counter("b"))))
        owner.begin_frame(CONSTRAINTS)
        calls: list[int] = []
        owner.on_frame_scheduled = lambda: calls.append(1)

        children = list(element.children)
        children[0].state.set_state()  # type: ignore[attr-defined]
        children[1].state.set_state()  # type: ignore[attr-defined]
        assert len(calls) == 1, "脏集合从空变非空才通知；已经非空了就不再通知"

    def test_marking_after_a_frame_notifies_again(self) -> None:
        owner = BuildOwner()
        state = self._counter(owner)
        calls: list[int] = []
        owner.on_frame_scheduled = lambda: calls.append(1)

        state.set_state()
        owner.begin_frame(CONSTRAINTS)
        state.set_state()
        assert len(calls) == 2, "帧末要注销「已通知」标志"

    def test_request_frame_is_the_paint_only_entry_point(self) -> None:
        """纯绘制脏不经过 mark_needs_build，由知道 owner 的那一层显式调它。"""
        owner = BuildOwner()
        calls: list[int] = []
        owner.on_frame_scheduled = lambda: calls.append(1)

        owner.request_frame()
        owner.request_frame()
        assert len(calls) == 1

        owner.begin_frame(CONSTRAINTS)
        owner.request_frame()
        assert len(calls) == 2

    def test_missing_callback_is_not_an_error(self) -> None:
        owner = BuildOwner()
        state = self._counter(owner)
        state.set_state()  # 没接回调也不该炸
        owner.begin_frame(CONSTRAINTS)
        assert owner.dirty_count == 0


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


class _PaintableBox(RenderObject):
    """会真的往显示列表里录一条指令的叶子——用来断言"到底画了没有"。

    `BoxRenderObject` 只数 paint 次数，看不出"画在哪"；滚动/resize 这类
    场景要断言的恰恰是**位置**变了，所以这里录一个实心矩形。
    """

    def __init__(self, width: float | None, height: float | None, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.box_width = width
        self.box_height = height

    def perform_layout(self, constraints: BoxConstraints) -> Size:
        width = self.box_width
        height = self.box_height
        if width is None:
            width = constraints.max_width if constraints.has_bounded_width else 0.0
        if height is None:
            height = constraints.max_height if constraints.has_bounded_height else 0.0
        return constraints.constrain(Size(width, height))

    def paint(self, context: object) -> None:  # type: ignore[override]
        fill = getattr(context, "fill_rect", None)
        if fill is not None:
            fill(Rect(0.0, 0.0, self.size.width, self.size.height), _PAINT_COLOR)


_PAINT_COLOR = Color(20, 30, 40)


def _record(root: RenderBox, width: int, height: int) -> DisplayList:
    """跑一趟绘制，返回这一趟录到的显示列表（不强制重画，只看脏跟踪）。"""
    recorder = DisplayListRecorder()
    root.paint_tree(recorder)
    return recorder.finish(width, height)


class TestLayoutMarksPaintDirty:
    """布局真的跑过 → 必须标 paint 脏。

    这是"静默断链"里最隐蔽的一条：纯几何变化（滚动、resize）不经过
    `mark_needs_paint`，所以此前滚完画面纹丝不动，而且没有任何报错。
    """

    def _scroll(self, count: int = 5) -> RenderScroll:
        content = RenderColumn(debug_name="List")
        for i in range(count):
            content.add(_PaintableBox(100.0, 40.0, debug_name=f"I{i}"))
        return RenderScroll(content, debug_name="Scroll")

    def test_scroll_to_produces_a_different_display_list(self) -> None:
        scroll = self._scroll()
        constraints = BoxConstraints(max_width=120, max_height=100)

        scroll.layout(constraints)
        before = _record(scroll, 120, 100)
        # R8.4 后指令里含状态指令（push-clip / push-translate / pop），
        # 断言语义要看**展开后**的纯绘制指令
        before_ops = list(resolve_state_ops(before.ops))
        assert len(before_ops) == 5, "首次布局后应当画出 5 条指令"

        scroll.scroll_to(dy=50)
        scroll.layout(constraints)
        after = _record(scroll, 120, 100)
        after_ops = list(resolve_state_ops(after.ops))

        assert len(after_ops) == 5, "滚动后必须是新指令，不是空增量"
        assert after_ops != before_ops, "滚动改变了子级位置，显示列表必须跟着变"

    def test_resize_produces_a_different_display_list(self) -> None:
        root = RenderColumn(debug_name="Root")
        root.add(_PaintableBox(None, 20.0, debug_name="Filler"))

        root.layout(BoxConstraints(max_width=100, max_height=100))
        before = _record(root, 100, 100)
        assert before.ops[0].rect.width == pytest.approx(100.0)

        root.layout(BoxConstraints(max_width=80, max_height=100))
        after = _record(root, 80, 100)
        assert after != before, "约束变了就必须重绘"
        assert after.ops[0].rect.width == pytest.approx(80.0)

    def test_cache_hit_does_not_mark_paint(self) -> None:
        """缓存命中（约束没变且不脏）不该白白标脏——否则每帧全量重画。"""
        root = RenderColumn(debug_name="Root")
        root.add(_PaintableBox(50.0, 20.0))
        constraints = BoxConstraints(max_width=100, max_height=100)

        root.layout(constraints)
        _record(root, 100, 100)
        assert not root.needs_paint

        root.layout(constraints)  # 完全相同的约束 → 走缓存
        assert not root.needs_paint, "缓存命中不该标 paint 脏"

    def test_clean_tree_is_still_pruned(self) -> None:
        """回归守卫：干净树重复绘制必须整棵剪掉（修复不许变成"每帧全量重画"）。"""
        root = RenderColumn(debug_name="Root")
        root.add(_PaintableBox(50.0, 20.0))
        constraints = BoxConstraints(max_width=100, max_height=100)

        root.layout(constraints)
        assert len(_record(root, 100, 100)) == 1
        assert len(_record(root, 100, 100)) == 0, "干净树第二次绘制应当一条指令都不产生"

    def test_relayout_marks_descendants_whose_own_layout_is_cached(self) -> None:
        """父级重排时，子级位置可能变，但子级自己的布局走缓存——也必须标脏。"""
        root = RenderColumn(debug_name="Root")
        leaf = _PaintableBox(50.0, 20.0, debug_name="Leaf")
        root.add(leaf)
        constraints = BoxConstraints(max_width=100, max_height=100)

        root.layout(constraints)
        _record(root, 100, 100)
        assert not leaf.needs_paint

        root.mark_needs_layout()  # 只标根：子级的布局会命中缓存
        root.layout(constraints)
        assert leaf.needs_paint, "父级重排后子级位置可能变了，必须跟着重绘"

    def test_mark_subtree_needs_paint_reaches_every_node(self) -> None:
        root = RenderColumn(debug_name="Root")
        mid = RenderColumn(debug_name="Mid")
        leaf = _PaintableBox(10.0, 10.0, debug_name="Leaf")
        root.add(mid)
        mid.add(leaf)
        constraints = BoxConstraints(max_width=100, max_height=100)
        root.layout(constraints)
        _record(root, 100, 100)

        # 只让叶子脏：冒泡会把祖先带上，但祖先的**其他**子级还是干净的——
        # 所以标脏时不能"节点已脏就提前收工"。
        sibling = _PaintableBox(10.0, 10.0, debug_name="Sibling")
        mid.add(sibling)
        root.layout(constraints)
        _record(root, 100, 100)
        assert not mid.needs_paint and not sibling.needs_paint

        leaf.mark_needs_paint()
        assert mid.needs_paint
        mid.mark_subtree_needs_paint()
        assert sibling.needs_paint, "子树标脏必须一路走到叶子"


class TestDebugPath:
    def test_path_shows_widget_types(self):
        owner = BuildOwner()
        element = owner.mount(Column((Box(), Box())))
        owner.begin_frame(CONSTRAINTS)
        assert element.describe_path() == "Column"
        assert element.children[0].describe_path() == "Column > Box[0]"
