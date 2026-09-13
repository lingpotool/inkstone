"""动效基座（R14.1）：排帧、标量动画、减少动效偏好。

这一层存在的理由只有一个：**让"随时间变化"在注入时钟下完全确定**。
所以测试全部围绕"推进到某一毫秒，值必须是什么"，不碰墙上时钟。
"""

from __future__ import annotations

import pytest

from inkstone.motion import AnimatedValue, Ticker, duration, prefers_reduced_motion, reduced_motion


class _Recorder:
    """记下每次被推到的时刻，并在指定帧后自行退出。"""

    def __init__(self, *, stop_after: int | None = None) -> None:
        self.ticks: list[float] = []
        self._stop_after = stop_after

    def tick(self, now_ms: float) -> bool:
        self.ticks.append(now_ms)
        if self._stop_after is None:
            return True
        return len(self.ticks) < self._stop_after


class TestTicker:
    def test_freezes_when_nobody_needs_frames(self) -> None:
        """空闲不留帧：没人要推进时必须返回 False。"""
        assert Ticker().tick(0.0) is False

    def test_reports_pending_while_any_member_wants_more(self) -> None:
        ticker = Ticker()
        ticker.add(_Recorder())
        assert ticker.tick(10.0) is True

    def test_finished_members_leave_the_set(self) -> None:
        ticker = Ticker()
        ticker.add(_Recorder(stop_after=1))
        assert ticker.tick(10.0) is False, "跑完的成员不该再让排帧继续"
        assert ticker.active == 0, "跑完就该退出集合，不等调用方清理"

    def test_add_is_idempotent(self) -> None:
        ticker = Ticker()
        item = _Recorder()
        ticker.add(item)
        ticker.add(item)
        ticker.tick(0.0)
        assert item.ticks == [0.0], "同一个成员不该被推进两次"

    def test_remove_stops_further_ticks(self) -> None:
        ticker = Ticker()
        item = _Recorder()
        ticker.add(item)
        ticker.remove(item)
        ticker.tick(0.0)
        assert item.ticks == []


class TestAnimatedValue:
    def test_instant_when_duration_is_zero(self) -> None:
        value = AnimatedValue(0.0, duration_ms=0.0)
        value.set_target(1.0, now_ms=0.0)
        assert value.value == 1.0
        assert value.running is False

    def test_interpolates_over_the_duration(self) -> None:
        value = AnimatedValue(0.0, duration_ms=100.0)
        value.set_target(1.0, now_ms=0.0)
        assert value.tick(0.0) is True
        assert value.value == pytest.approx(0.0)
        assert value.tick(50.0) is True
        assert value.value == pytest.approx(0.5)
        assert value.tick(100.0) is False, "到点就停"
        assert value.value == pytest.approx(1.0)

    def test_retarget_starts_from_the_current_value(self) -> None:
        """淡出到一半又被唤醒：从当前值往回走，不跳变。"""
        value = AnimatedValue(0.0, duration_ms=100.0)
        value.set_target(1.0, now_ms=0.0)
        value.tick(50.0)
        halfway = value.value
        value.set_target(0.0, now_ms=50.0)
        assert value.value == pytest.approx(halfway), "改目标不许跳变"
        value.tick(100.0)
        assert value.value == pytest.approx(halfway / 2.0, abs=1e-6)

    def test_tick_after_stop_is_a_no_op(self) -> None:
        value = AnimatedValue(0.0, duration_ms=100.0)
        value.set_target(1.0, now_ms=0.0)
        value.tick(100.0)
        assert value.tick(200.0) is False
        assert value.value == pytest.approx(1.0)

    def test_finish_jumps_to_the_target(self) -> None:
        """减少动效的降级路径：不播动画，直接到位。"""
        value = AnimatedValue(0.0, duration_ms=100.0)
        value.set_target(1.0, now_ms=0.0)
        value.finish()
        assert value.value == 1.0 and value.running is False

    def test_custom_easing_is_applied(self) -> None:
        value = AnimatedValue(0.0, duration_ms=100.0, easing=lambda t: t * t)
        value.set_target(1.0, now_ms=0.0)
        value.tick(50.0)
        assert value.value == pytest.approx(0.25)


class TestReducedMotion:
    @pytest.fixture(autouse=True)
    def _restore(self):
        reduced_motion.reset_cache()
        yield
        reduced_motion.reset_cache()

    def test_can_be_overridden_for_tests(self) -> None:
        reduced_motion.set_reduced_motion(True)
        assert prefers_reduced_motion() is True
        reduced_motion.set_reduced_motion(False)
        assert prefers_reduced_motion() is False

    def test_duration_collapses_to_zero_when_reduced(self) -> None:
        reduced_motion.set_reduced_motion(True)
        assert duration(200.0) == 0.0
        reduced_motion.set_reduced_motion(False)
        assert duration(200.0) == 200.0
