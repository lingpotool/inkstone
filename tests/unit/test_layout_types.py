"""布局基础类型的单元测试。

这些测试**不需要窗口、不需要显卡**，是"无头可测"这条原则的第一批证据。
"""

import pytest

from inkstone.layout import (
    INF,
    BoxConstraints,
    EdgeInsets,
    Rect,
    Size,
)


class TestSize:
    def test_empty_when_any_side_is_zero(self):
        assert Size(0, 10).is_empty
        assert Size(10, 0).is_empty
        assert not Size(10, 10).is_empty

    def test_contains(self):
        assert Size(100, 100).contains(Size(50, 50))
        assert not Size(50, 100).contains(Size(60, 10))

    def test_clamp(self):
        assert Size(5, 500).clamp(Size(10, 10), Size(20, 20)) == Size(10, 20)

    def test_immutable(self):
        s = Size(10, 10)
        # frozen dataclass 抛的是 FrozenInstanceError，它是 AttributeError 的子类
        with pytest.raises(AttributeError):
            s.width = 20  # type: ignore[misc]


class TestRect:
    def test_from_ltrb(self):
        assert Rect.from_ltrb(10, 20, 110, 70) == Rect(10, 20, 100, 50)

    def test_edges(self):
        r = Rect(10, 20, 100, 50)
        assert (r.right, r.bottom) == (110, 70)
        assert r.size == Size(100, 50)

    def test_contains_point(self):
        r = Rect(0, 0, 100, 100)
        assert r.contains(0, 0)
        assert r.contains(99.9, 99.9)
        assert not r.contains(101, 50)

    def test_intersect(self):
        a = Rect(0, 0, 100, 100)
        b = Rect(50, 50, 100, 100)
        assert a.intersect(b) == Rect(50, 50, 50, 50)
        assert not a.intersect(Rect(200, 200, 10, 10)).intersects(b)

    def test_inflate_and_deflate_are_inverse(self):
        r = Rect(10, 10, 80, 60)
        assert r.inflate(5, 5).deflate(5, 5) == r


class TestEdgeInsets:
    def test_from_list_variants(self):
        assert EdgeInsets.from_list([8]) == EdgeInsets.all(8)
        assert EdgeInsets.from_list([4, 8]) == EdgeInsets.symmetric(horizontal=8, vertical=4)
        assert EdgeInsets.from_list([1, 2, 3, 4]) == EdgeInsets(left=4, top=1, right=2, bottom=3)

    def test_from_list_rejects_bad_length(self):
        with pytest.raises(ValueError):
            EdgeInsets.from_list([1, 2, 3])

    def test_deflate_never_negative(self):
        insets = EdgeInsets.all(40)
        assert insets.deflate_size(Size(50, 50)) == Size(0, 0)

    def test_inflate_deflate_roundtrip(self):
        insets = EdgeInsets.only(left=1, top=2, right=3, bottom=4)
        assert insets.inflate_size(insets.deflate_size(Size(100, 100))) == Size(100, 100)


class TestBoxConstraints:
    def test_rejects_inverted_bounds(self):
        with pytest.raises(ValueError):
            BoxConstraints(min_width=100, max_width=10)

    def test_tight(self):
        c = BoxConstraints.tight(Size(80, 40))
        assert c.is_tight
        assert c.biggest == Size(80, 40)
        assert c.smallest == Size(80, 40)

    def test_loose(self):
        c = BoxConstraints.loose(Size(80, 40))
        assert not c.is_tight
        assert c.smallest == Size(0, 0)

    def test_constrain_clamps_into_range(self):
        c = BoxConstraints(min_width=50, max_width=100, min_height=0, max_height=60)
        assert c.constrain(Size(10, 10)) == Size(50, 10)
        assert c.constrain(Size(999, 999)) == Size(100, 60)
        assert c.constrain(Size(70, 30)) == Size(70, 30)

    def test_deflate_accounts_for_padding(self):
        c = BoxConstraints(0, 100, 0, 50)
        inner = c.deflate(EdgeInsets.symmetric(horizontal=10, vertical=5))
        assert inner.max_width == 80
        assert inner.max_height == 40

    def test_deflate_keeps_constraints_valid_when_padding_exceeds_space(self):
        c = BoxConstraints(0, 20, 0, 20)
        inner = c.deflate(EdgeInsets.all(30))
        assert inner.max_width == 0
        assert inner.min_width == 0

    def test_enforce_intersects_two_sets(self):
        a = BoxConstraints(0, 100, 0, 100)
        b = BoxConstraints(40, INF, 0, 30)
        merged = a.enforce(b)
        assert merged.min_width == 40
        assert merged.max_width == 100
        assert merged.max_height == 30

    def test_expand_is_infinite_in_both_axes(self):
        c = BoxConstraints.expand()
        assert not c.has_bounded_width
        assert not c.has_bounded_height
        assert c.has_infinite_width

    def test_flipped_swaps_axes(self):
        c = BoxConstraints(0, 100, 10, 20)
        assert c.flipped() == BoxConstraints(10, 20, 0, 100)

    def test_infinite_is_represented_as_float_inf(self):
        assert float("inf") == INF
