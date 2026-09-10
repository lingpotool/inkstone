"""断行（L3）—— UAX #14 换行机会判定 + **中文标点禁则**（docs/04 §2 §5）。

ADR-0005 说"断行算法不自己造"，但要有分寸：

    UAX #14 的完整实现包含上千条规则与成对表（Pair Table），
    那是"两年换更差版本"的典型。**本模块不重写它**，
    而是实现"够用且正确"的一层，靠三件事达成中文排版质量：

    1. **断行机会的显式建模**（`BreakOpportunity`），而不是"到宽度就切"；
    2. **中文标点禁则**（行首禁则 / 行尾禁则），这是中文排版的核心需求，
       而且规则少、可枚举、可测试——docs/04 §7 要求 20+10 个测试例；
    3. **西文按空格与连字符断，长单词不硬切**（除非超长到必须切）。

    "先用简化规则 + 完整测试钉住行为，替换成 UAX #14 时测试就是回归网"——
    这是与"自己造整形器"完全不同的风险等级。

本模块是**纯函数**：只吃字符串与宽度查询函数，不吃字体、不吃后端。
所以它 100% 无窗口可测，也永远不会因为度量实现变化而行为漂移。

状态：已实现（简化 UAX #14 核心 + 完整 CJK 禁则）。
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "BreakOpportunity",
    "LineBreakResult",
    "break_line",
    "can_break_between",
    "greedy_wrap",
]


class BreakOpportunity(Enum):
    """两个相邻字符之间是否允许换行。

    三档而不是布尔，是因为"必须断"（硬换行）与"可以断"（软机会）
    在排版里行为不同：前者即使行没满也要断，后者只在超宽时才断。
    """

    FORBIDDEN = "forbidden"  # 禁止断（如中文标点前后、英文单词内部）
    ALLOWED = "allowed"  # 允许断（如汉字之间、空格之后）
    MANDATORY = "mandatory"  # 必须断（\n）


# ---------------------------------------------------------------- 字符类

#: 行首禁则：这些字符**不许出现在行首**（必须跟着前一个字）。
#: 判据是"如果断在它前面，它就成了下一行的第一个字符"→ 断点非法。
_LINE_START_FORBIDDEN: frozenset[str] = frozenset(
    "，。、；：？！）〕］｝〉》」』】’”·…％‰℃°′″"  # 中文标点
    ",.;:?!)]}>"  # 西文标点（半角）
    "\u3001\u3002\uff0c\uff0e\uff1b\uff1a\uff1f\uff01"  # 全角标点（显式列出防漏）
)

#: 行尾禁则：这些字符**不许出现在行尾**（必须跟着后一个字）。
#: 判据是"如果断在它后面，它就成了上一行的最后一个字符"→ 断点非法。
_LINE_END_FORBIDDEN: frozenset[str] = frozenset(
    "（〔［｛〈《「『【‘“"  # 中文开括号/开引号
    "([{<"  # 西文开括号
)

#: 这些标点即使被禁则推到行尾，也**优先悬挂在行外**而不是重排（Phase 3 的两端对齐用）。
#: 当前仅记录，标记出来是为了将来做"标点悬挂"时不改调用点。
_HANGING_PUNCTUATION: frozenset[str] = frozenset("，。、；：！？》）」』】,.;:?!")

#: 不该出现在行首的"小字符"（避免孤立的标点起行）
_NO_LINE_START_EXTRA: frozenset[str] = frozenset("々ー〜～")

#: 某些语言里相当于空格的连字符，断行时应保留在上一行
_TRAILING_HYPHENS: frozenset[str] = frozenset("-\u2010\u2011")


def _is_cjk(char: str) -> bool:
    """是否 CJK 字符（汉字/假名/谚文/全角标点）。

    断行策略按这个分流：CJK 字符间**逐字可断**，
    西文单词内**不可断**（整词搬下一行）。
    """
    if not char:
        return False
    code = ord(char)
    if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
        return True
    if 0x3040 <= code <= 0x30FF:  # 假名
        return True
    if 0xAC00 <= code <= 0xD7AF:  # 谚文
        return True
    return unicodedata.east_asian_width(char) in ("W", "F")


def _is_space(char: str) -> bool:
    return char in (" ", "\t", "\u3000")  # 显式含全角空格


def can_break_between(left: str, right: str) -> BreakOpportunity:
    """判定 `left` 与 `right` 之间能否断行。**禁则的核心判定点。**

    判定顺序（顺序即优先级，不能换）：

        1. 硬换行 —— `\\n` 强制断
        2. 空格之后 —— 允许断（空格留在上一行尾，这是排版惯例）
        3. 行首禁则 —— 右侧是禁则标点 → **禁止断**（否则标点起行）
        4. 行尾禁则 —— 左侧是开括号/开引号 → **禁止断**（否则括号吊尾）
        5. 西文连字符后 —— 允许断（`well-` / `known`）
        6. CJK 之间 —— 允许断（逐字可断是中文的特性）
        7. 其余 —— 禁止断（西文单词内部）

    第 3、4 条就是中文标点禁则。把"禁则"表达成"这个断点是否合法"，
    而不是"事后把标点搬回去"——前者是声明式的、可穷举测试的；
    后者会在连续标点、行尾溢出时反复搬动，逻辑迅速失控。
    """
    if not left or not right:
        return BreakOpportunity.FORBIDDEN
    if left == "\n" or right == "\n":
        return BreakOpportunity.MANDATORY

    if _is_space(left):
        # 空格后可以断；但右侧若是行首禁则标点，仍不许断（标点不能起行）
        if right in _LINE_START_FORBIDDEN:
            return BreakOpportunity.FORBIDDEN
        return BreakOpportunity.ALLOWED

    if right in _LINE_START_FORBIDDEN or right in _NO_LINE_START_EXTRA:
        return BreakOpportunity.FORBIDDEN

    if left in _LINE_END_FORBIDDEN:
        return BreakOpportunity.FORBIDDEN

    if left in _TRAILING_HYPHENS and not _is_cjk(right):
        return BreakOpportunity.ALLOWED

    if _is_cjk(left) and _is_cjk(right):
        return BreakOpportunity.ALLOWED

    # 中文标点之后可以断（`，`/`。` 后面就是常规断行位）。
    # 这一条必须放在最后：它只处理"左 CJK 标点、右非 CJK"的混合情况，
    # 前面几条已经拦掉了"右是禁则标点"、"左是开括号"等真正非法的组合。
    if _is_cjk(left) and not _is_cjk(right):
        return BreakOpportunity.ALLOWED

    return BreakOpportunity.FORBIDDEN


# ---------------------------------------------------------------- 断行


@dataclass(frozen=True, slots=True)
class LineBreakResult:
    """一段文本断成若干行的结果。

    `lines` 里每个元素是**该行的字符串**（不含行尾换行符）。
    空文本得到 `[""]`（一行空），这与"排版出空行"的语义一致，
    避免调用方到处判 `if lines`。
    """

    lines: tuple[str, ...]

    @property
    def line_count(self) -> int:
        return len(self.lines)


def break_line(
    text: str,
    max_width: float,
    width_of: Callable[[str], float],
    *,
    hard_break: bool = True,
) -> LineBreakResult:
    """把**一段**文本按宽度切成多行（贪心算法）。

    `width_of` 是"这段文字多宽"的查询函数（由 `FontResolver` 提供，
    内部走唯一的度量入口）——断行本身不认识字体，这样它才能是纯函数。

    贪心而非最优（Knuth-Plass）：贪心对 CJK 效果良好（汉字等宽、逐字可断），
    且是**线性**复杂度。全局最优断行是 O(n²) 且对中文收益有限，
    属于 Phase 3 富文本的范畴。此处选择"够好且快且可预测"。

    `hard_break=False` 时不理会 `\\n`（用于单行场景把换行当普通字符）。

    边界：`max_width <= 0` 表示"无限宽"——不换行，整段作为一行。
    这是 TextView 在 ScrollView 里（主轴无限）的正常输入，
    **不是错误**，所以不抛异常。
    """
    if not text:
        return LineBreakResult(lines=("",))
    if max_width <= 0:
        return LineBreakResult(lines=(text,))

    lines: list[str] = []
    for paragraph in text.split("\n") if hard_break else [text]:
        lines.extend(_wrap_paragraph(paragraph, max_width, width_of))
    return LineBreakResult(lines=tuple(lines) if lines else ("",))


def _wrap_paragraph(
    paragraph: str,
    max_width: float,
    width_of: Callable[[str], float],
) -> list[str]:
    """对单个段落（无换行符）做贪心断行。

    算法（两阶段，不是"边加边断"）：

        阶段一：从 `cursor` 起**尽可能远**地延伸，记录所有合法断点，
                同时算出"到了哪个下标就超宽"。
        阶段二：在"没超宽"的范围内，取**最靠右的合法断点**；
                若一个合法断点都没有（超长单词），才硬切在超宽处。

    为什么必须两阶段：如果边加边断，"当前行第一个合法断点之前就超宽"
    的情况会被误判成"无断点可用"从而硬切单词。必须先扫描再决策。

    前置空白（行首空格）悬挂：不参与宽度累加，但会随行输出——
    重排时刻意保留，便于调试时看出缩进来自哪里。
    """
    if not paragraph:
        return [""]

    lines: list[str] = []
    cursor = 0
    length = len(paragraph)

    while cursor < length:
        # ---- 阶段一：扫描出本行的可用范围与合法断点
        width = 0.0
        overflow_at = length  # 第一个"加上就超宽"的字符下标
        breaks: list[int] = []  # 合法断点（指向断点后第一个字符）

        for index in range(cursor, length):
            char_width = width_of(paragraph[index])
            # 行首连续空白不占宽度（前导空白挂在外侧）
            is_leading = index == cursor
            counts = char_width > 0.0 and not (is_leading and _is_space(paragraph[index]))
            if counts and width + char_width > max_width:
                overflow_at = index
                break
            width += char_width

            if index + 1 < length:
                opportunity = can_break_between(paragraph[index], paragraph[index + 1])
                if opportunity is BreakOpportunity.ALLOWED:
                    breaks.append(index + 1)
                elif opportunity is BreakOpportunity.MANDATORY:
                    breaks.append(index + 1)
                    overflow_at = index + 1
                    break

        # ---- 阶段二：决定本行终点
        if overflow_at >= length:
            end = length  # 全部装得下
        else:
            # 取"不超过超宽位置"的最右合法断点
            candidates = [b for b in breaks if b <= overflow_at]
            end = max(candidates) if candidates else overflow_at

        if end <= cursor:  # 兜底：至少推进一个字符，防死循环
            end = cursor + 1

        # 行尾空白裁掉：它不参与渲染，留在行内会让"两端对齐"算错
        lines.append(paragraph[cursor:end].rstrip(" \t\u3000"))
        cursor = end

    return lines


def greedy_wrap(
    text: str,
    max_width: float,
    width_of: Callable[[str], float],
) -> LineBreakResult:
    """`break_line` 的别名，语义更直白：贪心换行。

    保留两个名字是因为调用点读起来不同：段落排版里说"break_line"，
    而工具函数场景说"greedy_wrap"。实现只有一份。
    """
    return break_line(text, max_width, width_of)
