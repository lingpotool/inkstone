"""显示列表 —— 与后端无关的绘制指令序列。

一帧渲染的中间产物，也是"黄金图测试"的物质基础：

    RenderObject.paint() → DisplayList（指令）→ RasterBackend → 像素

**为什么是显示列表而不是直接画**：同样的指令序列，GL 后端拿去上显卡，
软件光栅拿去画进内存缓冲区，检查器拿去序列化成文本——三者互不干扰。
它就是渲染管线的"中间表示"。

文档里那句"显示列表是**回放与比对的单位**"在这里落地：
指令全部是不可变数据类，坐标在录制时已经折算成绝对坐标，
所以 `dl1 == dl2` 就是逐指令逐坐标比对——确定性由此而来。

v0 的指令集刻意收敛：矩形填充 / 圆角填充 / 矩形描边 / 裁剪 / 文本。
阴影、渐变、路径属后续阶段（docs/03 的完整指令表）。

**文本指令只吃字形不吃字符串**（`TextRunOp`）：整形与断行在 L3 文本层
完成，渲染层只负责"把已定位的字形画出来"。这样换行规则、回退链、
字素簇这些知识不会渗进渲染层，渲染层也就不必知道"字"是什么。

状态：已实现（v0 指令集 + 文本）。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import Union

from ..layout.types import Offset, Rect
from .color import Color

__all__ = [
    "CLOSE",
    "CUBIC",
    "LINE",
    "MOVE",
    "PATH_VERBS",
    "QUAD",
    "DisplayList",
    "FillRectOp",
    "Op",
    "PaintOp",
    "PathCommand",
    "PathData",
    "PathFillOp",
    "PathStrokeOp",
    "PopLayerOp",
    "PopOp",
    "PositionedGlyph",
    "PushClipOp",
    "PushLayerOp",
    "PushTranslateOp",
    "StrokeRectOp",
    "TextRunOp",
    "resolve_state_ops",
]


# ---------------------------------------------------------------- 路径数据
#
# 路径用**扁平的 verb 数组**表示：`(verb, *coords)` 的序列，verb ∈ {M,L,Q,C,Z}。
#
# 为什么不接受 SVG path 字符串（docs/17 §R3.4 已定）：
#   1. 解析器进了渲染层，"同样的路径"就有两种写法（相对/绝对、隐含重复、
#      科学计数法），显示列表的**逐指令等值比对**立刻失效——黄金图与
#      "AI 生成界面"的确定性都建立在这条比对之上；
#   2. 路径字符串的解析要处理语法错误，而语法错误属于**调用方**的错，
#      应该在构造时当场炸掉，不是在光栅时画出一个半截图形。
#
# 用字符串常量而不是 Enum：路径数据要能原样序列化、能逐字节比对，
# 字符串没有格式化歧义（`f"{Enum成员}"` 在 3.10 与 3.11+ 行为不同）。

#: 移动到（不画）：`(M, x, y)`
MOVE = "M"
#: 直线到：`(L, x, y)`
LINE = "L"
#: 二次贝塞尔到：`(Q, cx, cy, x, y)`
QUAD = "Q"
#: 三次贝塞尔到：`(C, c1x, c1y, c2x, c2y, x, y)`
CUBIC = "C"
#: 闭合子路径：`(Z,)`
CLOSE = "Z"

PATH_VERBS: frozenset[str] = frozenset({MOVE, LINE, QUAD, CUBIC, CLOSE})

#: 每条命令需要的坐标个数
_PATH_ARITY: dict[str, int] = {MOVE: 2, LINE: 2, QUAD: 4, CUBIC: 6, CLOSE: 0}

#: 一条路径命令：`(verb, *coords)`
PathCommand = tuple[float | str, ...]
#: 整条路径：命令序列
PathData = tuple[PathCommand, ...]


def validate_path(data: PathData) -> None:
    """路径数据的形状检查。**在构造时炸，不在光栅时炸。**

    光栅端拿到非法路径只能"画一半然后放弃"，那是静默失败；
    构造时抛异常则把错误定位到写出这条路径的那一行代码。
    """
    if not data:
        raise ValueError(
            "路径是空的——它画不出任何东西，所以几乎总是调用方算错了"
            "（想表达「什么都不画」就别发这条指令，与空文本 run 同一个口径）"
        )
    for index, command in enumerate(data):
        if not command:
            raise ValueError(f"路径第 {index} 条命令是空的")
        verb = command[0]
        if not isinstance(verb, str) or verb not in _PATH_ARITY:
            raise ValueError(
                f"路径第 {index} 条命令的动词非法：{verb!r}"
                f"（可用：{', '.join(sorted(_PATH_ARITY))}）"
            )
        expected = _PATH_ARITY[verb]
        if len(command) != expected + 1:
            raise ValueError(
                f"路径第 {index} 条命令 {verb} 需要 {expected} 个坐标，收到 {len(command) - 1}"
            )
        for value in command[1:]:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"路径第 {index} 条命令 {verb} 的坐标必须是数字，收到 {value!r}")


@dataclass(frozen=True, slots=True)
class FillRectOp:
    """实心矩形填充；`radius > 0` 时是圆角矩形。"""

    rect: Rect
    color: Color
    radius: float = 0.0
    clip: Rect | None = None


@dataclass(frozen=True, slots=True)
class StrokeRectOp:
    """矩形描边（边框）。`width` 为线宽，向矩形内侧生长。"""

    rect: Rect
    color: Color
    width: float
    radius: float = 0.0
    clip: Rect | None = None


@dataclass(frozen=True, slots=True)
class PositionedGlyph:
    """一个**已定位**的字形簇：文本 + 相对 run 原点的位置。

    `text` 是这一簇的源文本（通常 1 个字符，emoji 序列可能几个码点）。
    为什么带着文本而不是"字形 ID"：字形 ID 是字体内部编号，需要后端
    在加载字体时分配；在字体后端落地前，光栅层靠文本查内置字形表。
    真字形 ID 落地时给本类加一个可选字段即可，指令形状不变。

    四个偏移量与 HarfBuzz 的 `hb_glyph_position_t` 一一对应
    （`x_offset / y_offset / x_advance / y_advance`），这样整形结果可以
    **原样**落进显示列表，中间不做任何解释。
    """

    text: str
    #: 字形左下角相对 run 原点的水平偏移（向右为正）。
    x: float
    #: 该簇的推进宽度（来自文本层的度量，**不是光栅层自己算的**）。
    advance: float
    #: 承载它的字体族（回退链可能让同一行来自多个字体）。
    family: str = ""
    #: 该簇的 em 高度（用于占位字形定尺寸）。
    em: float = 0.0
    #: 该簇在字体里的**字形 id 序列**（整形结果，可能不止一个：基字符 + 组合符）。
    #:
    #: 为什么需要它：连字（"ffi" → 一个字形覆盖三个字素簇）在显示列表里被切成
    #: 三个 `PositionedGlyph`，光栅层若按文本逐簇取掩码，会拿到三个独立字形
    #: （f/f/i）——**排版按连字算宽度、画出来却是分开的字母**，两者对不上。
    #: 按 id 取掩码则直接命中那个连字字形。
    #:
    #: 空元组 = "不知道"（内置确定性后端没有字形 id 概念），光栅层退回按文本取。
    glyph_ids: tuple[int, ...] = ()
    #: 字形相对基线的**垂直**偏移（向上为正）。
    #:
    #: 为什么必须有它：HarfBuzz 的输出是四元组，上下标、组合符、
    #: CJK 标点悬挂、多字体回退的基线差全都靠 `y_offset` 表达。
    #: 只有 `x` 的话，R4（HarfBuzz 文本栈）落地即撞墙——
    #: 表现是"组合符全跑到基线上、上下标不会上下"。
    y_offset: float = 0.0
    #: `glyph_ids` 所属**具体字体面**的标识（R7.4）。
    #:
    #: 同一 family 的 Regular 与 Bold 是两个文件、两套字形编号；光栅若只按
    #: `family` 重选面，就可能用甲的 id 查乙的轮廓、画出完全不同的字。
    #: 整形把选中的面记在这里，一路传到 `GlyphProvider.mask_for(face_key=...)`。
    face_key: str = ""


@dataclass(frozen=True, slots=True)
class TextRunOp:
    """一段文本的绘制指令 —— docs/03 所称的 `text_run`。

    **不接受字符串**（docs/03 §指令表）：整形与断行在文本层完成，
    这条指令只承载"哪些字形、画在哪"。这是渲染层唯一认识的文本形态，
    所以渲染层永远不需要知道换行规则、回退链、字素簇——那些是 L3 的事。

    `baseline` 是基线相对 `origin.y` 的偏移。**不用"行顶"定位**：
    不同字体的 ascent 不同，用行顶定位会让混排的基线参差不齐，
    而基线是字体排印里唯一稳定的对齐基准。
    """

    origin: Offset
    baseline: float
    glyphs: tuple[PositionedGlyph, ...]
    size: float
    color: Color
    #: 是否绘制组合态下划线（IME 未上屏的拼音）。Phase 2 IME 用。
    underline: bool = False
    clip: Rect | None = None


@dataclass(frozen=True, slots=True)
class PathFillOp:
    """填充一条路径。`data` 是扁平 verb 数组（见本模块顶部的说明）。

    为什么现在就定形状、光栅端却先不实现：`icons/stroke.py`（docs/13 承诺的
    SVG 图标）需要这个载体，而**定形状比实现光栅便宜得多**——
    等图标落地再改显示列表，就是一次破坏性变更。
    """

    data: PathData
    color: Color
    clip: Rect | None = None

    def __post_init__(self) -> None:
        validate_path(self.data)


@dataclass(frozen=True, slots=True)
class PathStrokeOp:
    """描边一条路径。`width` 是线宽。"""

    data: PathData
    color: Color
    width: float
    clip: Rect | None = None

    def __post_init__(self) -> None:
        validate_path(self.data)


# ---------------------------------------------------------------- 状态指令
#
# R8.4 引入"运行时状态"：平移与裁剪不再只能被录制器**烘焙**进每条指令，
# 也可以作为状态指令在回放时生效。这是"内容只录一次、每帧只改变换"（滚动层
# 缓存）的前提——否则滚动每帧都要把新偏移重新烤进几百条指令的坐标里。
#
# 只支持**平移**，不引入任意仿射：旋转/缩放的指令形状会牵动字形度量与圆角
# 语义（见 ADR-0015 的非等比例外），而那些能力的消费者（motion 的缩放动画）
# 还没落地。明确不支持，好过半个实现悄悄画错。


@dataclass(frozen=True, slots=True)
class PushTranslateOp:
    """压入一个平移：其后的绘制指令坐标整体偏移 `(dx, dy)`。与 `PopOp` 配对。"""

    dx: float
    dy: float


@dataclass(frozen=True, slots=True)
class PushClipOp:
    """压入一个裁剪：其后的绘制只保留与 `rect` 的交集。只能收窄。"""

    rect: Rect


@dataclass(frozen=True, slots=True)
class PopOp:
    """弹出最近一次 `PushTranslateOp` / `PushClipOp` 的状态。"""


@dataclass(frozen=True, slots=True)
class PushLayerOp:
    """标记一段**可缓存的层**（R8.4 后半，Flutter RasterCache 的思路）。

    `key` 是内容标识：只要内容没变，key 就不变，后端可以把这一层**渲染进
    离屏纹理缓存**，之后每帧只画一个四边形，而不是把几百条指令重新提交一遍。
    滚动正是这个模式——内容静态、只有平移在变。

    `rect` 是该层在**当前帧坐标系**里的边界（绝对像素）。
    """

    key: int
    rect: Rect


@dataclass(frozen=True, slots=True)
class PopLayerOp:
    """结束最近一次 `PushLayerOp`。"""


PaintOp = Union[  # noqa: UP007
    FillRectOp,
    StrokeRectOp,
    TextRunOp,
    PathFillOp,
    PathStrokeOp,
]


def resolve_state_ops(ops: tuple[Op, ...]) -> Iterator[PaintOp]:
    """把状态指令展开成等价的**纯绘制指令**序列。

    语义与"录制时直接烘焙"逐位一致（平移就是坐标相加、裁剪就是矩形取交），
    所以两个光栅后端只要都走这个函数，就能确定性地得到同一结果。
    坐标口径：状态里的裁剪是**屏幕坐标**，指令自带的 `clip` 是**指令局部坐标**
    （会被同一平移偏移后再取交）。
    """
    stack: list[tuple[float, float, Rect | None]] = [(0.0, 0.0, None)]
    balanced = True
    for op in ops:
        dx, dy, clip = stack[-1]
        if isinstance(op, PushTranslateOp):
            stack.append((dx + op.dx, dy + op.dy, clip))
            continue
        if isinstance(op, PushClipOp):
            rect = op.rect.shift(dx, dy)
            stack.append((dx, dy, rect if clip is None else clip.intersect(rect)))
            continue
        if isinstance(op, PopOp):
            if len(stack) == 1:
                raise ValueError("PopOp 没有配对的 Push——状态栈已空")
            stack.pop()
            continue
        if isinstance(op, (PushLayerOp, PopLayerOp)):
            # 层标记由关心缓存的后端处理；对"只要像素对"的路径（软件光栅）透明。
            # 层内部的状态指令仍由本函数展开，所以这里的跳过不会丢语义。
            continue
        resolved = _translate_op(op, dx, dy)
        yield _with_clip(resolved, _intersect_clip(clip, resolved.clip))
    if len(stack) != 1:
        balanced = False
    if not balanced:
        raise ValueError("有 Push 没配对的 Pop——状态指令必须成对")


def _translate_op(op: PaintOp, dx: float, dy: float) -> PaintOp:
    if dx == 0.0 and dy == 0.0:
        return op
    if isinstance(op, FillRectOp):
        return replace(op, rect=op.rect.shift(dx, dy), clip=_shift(op.clip, dx, dy))
    if isinstance(op, StrokeRectOp):
        return replace(op, rect=op.rect.shift(dx, dy), clip=_shift(op.clip, dx, dy))
    if isinstance(op, TextRunOp):
        return replace(
            op,
            origin=Offset(op.origin.dx + dx, op.origin.dy + dy),
            clip=_shift(op.clip, dx, dy),
        )
    if isinstance(op, PathFillOp):
        return replace(op, data=_shift_path(op.data, dx, dy), clip=_shift(op.clip, dx, dy))
    if isinstance(op, PathStrokeOp):
        return replace(op, data=_shift_path(op.data, dx, dy), clip=_shift(op.clip, dx, dy))
    raise NotImplementedError(f"状态平移不认识指令 {type(op).__name__}")


def _shift(rect: Rect | None, dx: float, dy: float) -> Rect | None:
    return None if rect is None else rect.shift(dx, dy)


def _with_clip(op: PaintOp, clip: Rect | None) -> PaintOp:
    return replace(op, clip=clip)


def _intersect_clip(a: Rect | None, b: Rect | None) -> Rect | None:
    if a is None:
        return b
    if b is None:
        return a
    return a.intersect(b)


def _shift_path(data: PathData, dx: float, dy: float) -> PathData:
    shifted: list[PathCommand] = []
    for command in data:
        verb = command[0]
        coords = command[1:]
        if verb == CLOSE:
            shifted.append(command)
            continue
        moved: list[float | str] = [verb]
        for index, value in enumerate(coords):
            assert isinstance(value, (int, float))
            moved.append(float(value) + (dx if index % 2 == 0 else dy))
        shifted.append(tuple(moved))
    return tuple(shifted)


# 指令联合类型。新增指令时只扩这里，光栅端同步加一个分支。
Op = Union[  # noqa: UP007
    FillRectOp,
    StrokeRectOp,
    TextRunOp,
    PathFillOp,
    PathStrokeOp,
    PushTranslateOp,
    PushClipOp,
    PopOp,
    PushLayerOp,
    PopLayerOp,
]


@dataclass(frozen=True, slots=True)
class DisplayList:
    """一帧的完整绘制指令 + 画布尺寸。"""

    width: int
    height: int
    ops: tuple[Op, ...]

    def __len__(self) -> int:
        return len(self.ops)

    def describe(self) -> str:
        """检查器用的可读文本。确定性调试从这里来。"""
        lines = [f"DisplayList({self.width}×{self.height}, {len(self.ops)} ops)"]
        for index, op in enumerate(self.ops):
            lines.append(f"  [{index}] {_describe_op(op)}")
        return "\n".join(lines)


def _describe_op(op: Op) -> str:
    if isinstance(op, PushTranslateOp):
        return f"push-translate ({op.dx:g},{op.dy:g})"
    if isinstance(op, PushClipOp):
        return f"push-clip {_r(op.rect)}"
    if isinstance(op, PushLayerOp):
        return f"push-layer key={op.key} {_r(op.rect)}"
    if isinstance(op, PopLayerOp):
        return "pop-layer"
    if isinstance(op, PopOp):
        return "pop"
    if isinstance(op, FillRectOp):
        radius = f" r={op.radius:g}" if op.radius > 0 else ""
        clip = f" clip={_r(op.clip)}" if op.clip else ""
        return f"fill {_r(op.rect)}{radius} {op.color}{clip}"
    if isinstance(op, TextRunOp):
        clip = f" clip={_r(op.clip)}" if op.clip else ""
        # 只报"多少个字形 + 前几个字"，避免检查器输出被长文本淹没
        preview = "".join(g.text for g in op.glyphs[:12])
        if len(op.glyphs) > 12:
            preview += "…"
        return (
            f"text {_o(op.origin)} baseline={op.baseline:g} size={op.size:g} "
            f"n={len(op.glyphs)} {op.color} {preview!r}{clip}"
        )
    if isinstance(op, (PathFillOp, PathStrokeOp)):
        clip = f" clip={_r(op.clip)}" if op.clip else ""
        # 路径可能很长：报"多少条命令 + 动词序列"，不把坐标全打出来
        verbs = "".join(str(c[0]) for c in op.data)
        if isinstance(op, PathStrokeOp):
            return f"path-stroke w={op.width:g} n={len(op.data)} [{verbs}] {op.color}{clip}"
        return f"path-fill n={len(op.data)} [{verbs}] {op.color}{clip}"
    assert isinstance(op, StrokeRectOp)
    return (
        f"stroke {_r(op.rect)} w={op.width:g}"
        f"{' r=' + format(op.radius, 'g') if op.radius > 0 else ''} {op.color}"
        + (f" clip={_r(op.clip)}" if op.clip else "")
    )


def _o(offset: Offset) -> str:
    return f"({offset.dx:g},{offset.dy:g})"


def _r(rect: Rect | None) -> str:
    if rect is None:
        return "none"
    return f"({rect.left:g},{rect.top:g} {rect.width:g}×{rect.height:g})"
