"""signals（R6.2，docs/06 §3 / docs/20）的单元测试。

验收项：

- build 期间读信号 = 登记依赖；写信号 = 只标脏读过它的组件
- 1000 次写只触发 1 次帧（批处理，`-m slow` 性能用例）
- Computed 缓存 + 惰性 + 脏传播
- Effect：有 owner 排队到下一帧 build 之前，无 owner 同步重跑
"""

import pytest

from inkstone.core import (
    BuildOwner,
    Computed,
    Effect,
    Element,
    FrameError,
    Key,
    LeafRenderObjectElement,
    MultiChildRenderObjectElement,
    RenderObject,
    RenderObjectWidget,
    Signal,
    State,
    StatefulWidget,
    StatelessWidget,
    Widget,
)
from inkstone.layout import BoxConstraints, RenderBox, RenderColumn, Size

# ---------------------------------------------------------------- 脚手架


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


class SignalProbe(StatelessWidget):
    """build 时读信号并记录读到的值——依赖登记的探针。"""

    def __init__(self, signal: Signal[int], log: list[int]) -> None:
        super().__init__()
        self.signal = signal
        self.log = log

    def build(self, context: Element) -> Widget:
        self.log.append(self.signal.value)
        return Box()


class PlainProbe(StatelessWidget):
    """不读任何信号的对照组。"""

    def __init__(self, log: list[str]) -> None:
        super().__init__()
        self.log = log

    def build(self, context: Element) -> Widget:
        self.log.append("build")
        return Box()


CONSTRAINTS = BoxConstraints(max_width=400, max_height=400)

# ---------------------------------------------------------------- Signal


class TestSignalDependencies:
    def test_write_dirties_only_consumers(self):
        sig = Signal(0)
        consumer_log: list[int] = []
        bystander_log: list[str] = []
        owner = BuildOwner()
        owner.mount(Column((SignalProbe(sig, consumer_log), PlainProbe(bystander_log))))
        owner.begin_frame(CONSTRAINTS)
        consumer_log.clear()
        bystander_log.clear()

        sig.value = 42
        owner.begin_frame(CONSTRAINTS)

        assert consumer_log == [42], "依赖者必须被标脏重建"
        assert bystander_log == [], "没读过信号的组件一次都不许重建"

    def test_peek_does_not_register_a_dependency(self):
        sig = Signal(0)
        log: list[str] = []

        class PeekProbe(StatelessWidget):
            def build(self, context: Element) -> Widget:
                sig.peek()
                log.append("build")
                return Box()

        owner = BuildOwner()
        owner.mount(PeekProbe())
        owner.begin_frame(CONSTRAINTS)
        log.clear()

        sig.value = 1
        assert owner.dirty_count == 0, "peek 是'只是看一眼'，不许登记依赖"

    def test_dependency_is_dropped_when_no_longer_read(self):
        """依赖每次 build 重新登记：这帧没读到的信号，写了不许再标脏。"""
        gate = Signal(True)
        data = Signal(0)
        log: list[str] = []

        class ConditionalProbe(StatelessWidget):
            def build(self, context: Element) -> Widget:
                if gate.value:
                    _ = data.value  # 只在 gate 打开时读
                log.append("build")
                return Box()

        owner = BuildOwner()
        owner.mount(ConditionalProbe())
        owner.begin_frame(CONSTRAINTS)
        log.clear()

        gate.value = False  # 触发重建；新 build 不再读 data
        owner.begin_frame(CONSTRAINTS)
        assert log == ["build"]
        log.clear()

        data.value = 99
        assert owner.dirty_count == 0, "不再读取的信号必须已从依赖里移除"

    def test_unmounted_consumer_is_unsubscribed(self):
        sig = Signal(0)
        log: list[int] = []
        owner = BuildOwner()
        owner.mount(Column((SignalProbe(sig, log),)))
        owner.begin_frame(CONSTRAINTS)

        owner.root.update(Column(()))  # type: ignore[union-attr]
        owner.begin_frame(CONSTRAINTS)
        assert not sig._dependents, "卸载必须解除订阅，否则信号永远标脏一个死节点"

    def test_write_during_layout_raises(self):
        """layout 阶段写信号 = 时序 bug，必须响亮地失败（与 set_state 同一守卫）。"""
        sig = Signal(0)
        log: list[int] = []
        owner = BuildOwner()
        owner.mount(SignalProbe(sig, log))
        owner.begin_frame(CONSTRAINTS)

        root = owner.root_render_object
        assert root is not None
        original = root.layout

        def naughty(constraints: BoxConstraints) -> Size:
            sig.value = 1
            return original(constraints)

        root.layout = naughty  # type: ignore[method-assign]
        with pytest.raises(FrameError):
            owner.begin_frame(CONSTRAINTS)


@pytest.mark.slow
class TestSignalBatchingPerformance:
    """docs/20 R6.2 性能验收：1000 次写只触发 1 次帧、只重建依赖者。"""

    def test_a_thousand_writes_trigger_one_frame(self):
        sig = Signal(0)
        consumer_log: list[int] = []
        bystander_log: list[str] = []
        owner = BuildOwner()
        owner.mount(Column((SignalProbe(sig, consumer_log), PlainProbe(bystander_log))))
        owner.begin_frame(CONSTRAINTS)
        consumer_log.clear()

        scheduled: list[int] = []
        owner.on_frame_scheduled = lambda: scheduled.append(1)
        for i in range(1, 1001):
            sig.value = i

        assert scheduled == [1], "1000 次写只许通知一次帧调度"
        owner.begin_frame(CONSTRAINTS)
        assert consumer_log == [1000], "一帧内只重建一次，读到的是最终值"
        assert bystander_log == ["build"], "对照组全程只 build 过挂载那一次"


# ---------------------------------------------------------------- Computed


class TestComputed:
    def test_caches_until_a_dependency_changes(self):
        calls: list[int] = []
        price = Signal(10.0)
        qty = Signal(2)
        total = Computed(lambda: calls.append(1) or price.value * qty.value)

        assert total.value == 20.0
        assert total.value == 20.0
        assert len(calls) == 1, "依赖没变就必须拿缓存"

        price.value = 12.0
        assert len(calls) == 1, "上游变了只标脏，不急着重算（惰性）"
        assert total.value == 24.0
        assert len(calls) == 2

    def test_dirty_propagates_through_chains(self):
        """信号 → Computed → 组件：底层写一次，链路上的消费者被标脏。"""
        base = Signal(1)
        derived = Computed(lambda: base.value * 10)
        log: list[int] = []

        class ComputedProbe(StatelessWidget):
            def build(self, context: Element) -> Widget:
                log.append(derived.value)
                return Box()

        owner = BuildOwner()
        owner.mount(ComputedProbe())
        owner.begin_frame(CONSTRAINTS)
        assert log == [10]

        base.value = 2
        owner.begin_frame(CONSTRAINTS)
        assert log == [10, 20], "Computed 必须把脏传给它的下游"


# ---------------------------------------------------------------- Effect


class TestEffect:
    def test_without_owner_reruns_synchronously(self):
        sig = Signal(0)
        seen: list[int] = []
        effect = Effect(lambda: seen.append(sig.value))

        sig.value = 1
        sig.value = 2
        assert seen == [0, 1, 2], "无 owner 的 effect 同步重跑"
        effect.dispose()

    def test_with_owner_flushes_at_next_frame_before_build(self):
        """effect 在 build 之前重跑：它 set_state 产生的脏同一帧被收掉。"""
        sig = Signal(0)
        order: list[str] = []

        class Mirror(StatefulWidget):
            def create_state(self) -> "MirrorState":
                return MirrorState()

        class MirrorState(State[Mirror]):
            def init_state(self) -> None:
                self.count = -1

            def build(self, context: Element) -> Widget:
                order.append("build")
                return Box()

        owner = BuildOwner()
        element = owner.mount(Mirror())
        owner.begin_frame(CONSTRAINTS)
        state = element.state  # type: ignore[attr-defined]
        order.clear()

        effect = Effect(
            lambda: (
                order.append("effect"),
                state.set_state(lambda: setattr(state, "count", sig.value)),
            ),
            owner=owner,
        )
        order.clear()

        sig.value = 7
        assert order == [], "帧之前 effect 不许抢跑"
        frames_before = owner.frame_count
        owner.begin_frame(CONSTRAINTS)

        assert owner.frame_count == frames_before + 1, "effect 与重建必须同帧结算"
        assert order == ["effect", "build"], "effect 必须先于 build 跑"
        assert state.count == 7, "effect 里 set_state 的结果必须本帧生效"
        effect.dispose()

    def test_multiple_invalidations_batch_into_one_run(self):
        sig = Signal(0)
        runs: list[int] = []
        owner = BuildOwner()
        effect = Effect(lambda: runs.append(sig.value), owner=owner)
        runs.clear()

        sig.value = 1
        sig.value = 2
        owner.begin_frame(CONSTRAINTS)
        assert runs == [2], "一帧内多次失效只重跑一次"
        effect.dispose()

    def test_dispose_stops_everything(self):
        sig = Signal(0)
        runs: list[int] = []
        effect = Effect(lambda: runs.append(sig.value))
        effect.dispose()

        sig.value = 1
        assert runs == [0], "dispose 之后不许再重跑"
        assert not sig._dependents, "dispose 必须解除订阅"
