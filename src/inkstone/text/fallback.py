"""字体回退链（L3）—— 中文/日文/emoji 不出现豆腐块的前提（docs/04 §4）。

分工（与 `font.py` 划清）：

    `font.py`     回答"系统里有没有雅黑"           —— 家族级探测
    本模块        回答"这个「汉」字该用哪个字体画"  —— **逐字符**归类

回退不是"挑一个中文字体用到底"，而是**逐字符、按脚本分级**：

    用户字体 → 拉丁字体（够用就用）→ 中文回退链 → emoji 链 → 系统默认

为什么要逐字符：中英混排里，拉丁字母要留在用户指定的字体里（否则
英文会变成中文字体的全宽拉丁，观感立刻垮掉），而汉字必须跳到中文字体。
**整个字符串用一个字体 = 观感错误；每个字符各自量 = 布局正确。**

平台链（docs/04 §4 原文，一字不改地实现）：

    Windows  用户字体 → Microsoft YaHei UI → SimSun → 系统默认
    macOS    用户字体 → PingFang SC → Hiragino Sans GB → 系统默认
    Linux    用户字体 → Noto Sans CJK SC → 文泉驿 → fontconfig 默认

本模块**不 import 平台模块**：上面这张表是数据，不是分支。
选到哪个平台，由 `FontResolver.has_family()` 的探测结果决定——
所以同一份代码在三平台各自落到各自有的字体上，而代码里没有 `if win32`。

状态：已实现。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache

from .font import FontResolver

__all__ = [
    "FallbackChain",
    "FontScript",
    "ScriptRun",
    "default_chain",
    "script_of",
    "split_by_script",
]


class FontScript(Enum):
    """粗粒度的文字系统分类。**够用就好**——回退链只需要区分
    "这字符该走哪个字体链"，不需要完整的 Unicode Script 属性表。

    细分的脚本（如把 Han 拆成简/繁）对字体选择没有额外价值，
    因为三平台的中文字体链只按"中日韩"分，不按简繁分。
    """

    COMMON = "common"  # 拉丁、数字、标点等，跟随主字体
    HAN = "han"  # 汉字（中日韩统一表意文字）
    KANA = "kana"  # 日文假名
    HANGUL = "hangul"  # 韩文谚文
    EMOJI = "emoji"  # 彩色 emoji
    SYMBOL = "symbol"  # 数学符号、箭头、货币等非 emoji 符号


# 按脚本判定用码点区间。Unicode 的汉字分散在多个区块，
# 逐个列举是唯一可靠办法（`unicodedata` 不直接给 Script 属性）。
_HAN_RANGES: tuple[tuple[int, int], ...] = (
    (0x3400, 0x4DBF),  # 扩展 A
    (0x4E00, 0x9FFF),  # 基本区
    (0xF900, 0xFAFF),  # 兼容表意文字
    (0x20000, 0x2A6DF),  # 扩展 B
    (0x2A700, 0x2EBEF),  # 扩展 C–F
    (0x30000, 0x3134F),  # 扩展 G
    (0x2F800, 0x2FA1F),  # 兼容补充
)
_KANA_RANGES: tuple[tuple[int, int], ...] = (
    (0x3040, 0x309F),  # 平假名
    (0x30A0, 0x30FF),  # 片假名
    (0x31F0, 0x31FF),  # 片假名音标扩展
)
_HANGUL_RANGES: tuple[tuple[int, int], ...] = (
    (0x1100, 0x11FF),  # 字母
    (0x3130, 0x318F),  # 兼容字母
    (0xAC00, 0xD7AF),  # 音节
)
_EMOJI_RANGES: tuple[tuple[int, int], ...] = (
    (0x1F300, 0x1F5FF),  # 杂项符号与图形
    (0x1F600, 0x1F64F),  # 表情
    (0x1F680, 0x1F6FF),  # 交通与地图
    (0x1F900, 0x1F9FF),  # 补充符号
    (0x1FA70, 0x1FAFF),  # 扩展 A
    (0x2600, 0x27BF),  # 杂项符号 + 装饰符号
    (0xFE00, 0xFE0F),  # 变体选择符（跟着前一个 emoji）
)


def _in_ranges(code: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(low <= code <= high for low, high in ranges)


@lru_cache(maxsize=8192)
def script_of(char: str) -> FontScript:
    """判断单个字符属于哪个文字系统。

    判定顺序有讲究：**先查 emoji，再查宽字符**——
    因为 emoji 里有些码点在 East Asian Width 里是 `W`，
    先判宽字符会把它们错分到 CJK 链，结果 emoji 用了中文字体渲染成黑白方框。

    纯函数 + 逐字符调用，所以加 `lru_cache`（R8.3）：断行时每个字符都要问一次，
    实测文本密集页 14 帧 5.6 万次调用，其中绝大多数是重复字符。
    """
    return _script_of_uncached(char)


def _script_of_uncached(char: str) -> FontScript:
    if not char:
        return FontScript.COMMON

    code = ord(char)
    if _in_ranges(code, _EMOJI_RANGES):
        return FontScript.EMOJI
    if _in_ranges(code, _HAN_RANGES):
        return FontScript.HAN
    if _in_ranges(code, _KANA_RANGES):
        return FontScript.KANA
    if _in_ranges(code, _HANGUL_RANGES):
        return FontScript.HANGUL

    # 全宽/宽字符（East Asian Width = W/F）归 CJK 链。
    # 这覆盖了全角标点（，。！？）、全角空格、以及上面区间没列到的汉字扩展区。
    # **必须放在 emoji 与 CJK 区间判定之后**——emoji 里也有 W 宽字符，
    # 先判宽会把 emoji 送进中文字体画成黑白方框。
    if unicodedata.east_asian_width(char) in ("W", "F"):
        return FontScript.HAN

    category = unicodedata.category(char)
    # 数学/货币/箭头类的非 ASCII 符号，独立成类，将来可接"符号字体链"
    if category.startswith("S") and not char.isascii():
        return FontScript.SYMBOL
    return FontScript.COMMON


@dataclass(frozen=True, slots=True)
class ScriptRun:
    """一段**同脚本**的连续字符。回退的最小单位。

    `[start, end)` 是它在源字符串里的下标区间。
    文本会被切成若干 run，每个 run 用一个字体家族——这就是"逐字符归类"
    的结果落点，也是排版时分行提交绘制的依据（同 run 同字体，可批量画）。
    """

    script: FontScript
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start


def split_by_script(text: str) -> list[ScriptRun]:
    """把文本切成同脚本的连续段。

    相邻的 COMMON 与其它脚本**不合并**：中英混排时 "你好Hello" 是两个 run，
    这样英文能用拉丁字体、汉字能用中文字体。但连续同类会合并，
    避免 "Hello" 被切成五个单字符 run 导致逐字查字体。

    **切分按字素簇，不按码点。** 这是必须的：`👨\\u200d👩\\u200d👧` 里
    的零宽连接符 `\\u200d` 本身没有脚本归属，按码点切会把它拆成独立的
    COMMON run，于是家庭 emoji 被切成 5 段、逐段找字体会直接碎掉。
    簇的脚本取**基字符**的判定，簇内其余字符跟随。
    """
    if not text:
        return []

    from ..backend.headless_fonts import grapheme_clusters

    clusters = grapheme_clusters(text)
    runs: list[ScriptRun] = []
    current_script = script_of(text[clusters[0][0]])
    start = clusters[0][0]
    for cluster_start, _cluster_end in clusters[1:]:
        script = script_of(text[cluster_start])
        if script is not current_script:
            runs.append(ScriptRun(current_script, start, cluster_start))
            current_script = script
            start = cluster_start
    runs.append(ScriptRun(current_script, start, len(text)))
    return runs


@dataclass(frozen=True, slots=True)
class _PlatformChain:
    """一个平台的中文/日文/韩文回退候选（数据，不是分支）。"""

    cjk: tuple[str, ...]
    emoji: tuple[str, ...]


# docs/04 §4 的三张表。**三条链都列出来，让探测结果自己选**——
# 这样在 Windows 上跑 macOS 链的代码也不会出错（只是全不命中然后兜底），
# 比"按平台只带一条链"更健壮，且代码里没有平台分支。
_WINDOWS = _PlatformChain(
    cjk=("Microsoft YaHei UI", "Microsoft YaHei", "SimSun", "SimHei"),
    emoji=("Segoe UI Emoji", "Segoe UI Symbol"),
)
_MACOS = _PlatformChain(
    cjk=("PingFang SC", "Hiragino Sans GB", "STHeiti", "Heiti SC"),
    emoji=("Apple Color Emoji",),
)
_LINUX = _PlatformChain(
    cjk=("Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei", "Droid Sans Fallback"),
    emoji=("Noto Color Emoji", "Noto Emoji"),
)


@dataclass(frozen=False)
class FallbackChain:
    """回退链的计算器。

    它不是"字体表"，而是"**给定用户想要的字体链，算出各脚本该用什么**"。
    结果按脚本缓存，因为同一套样式会被反复查询（每个字符一次）。

    注意：**不加 `slots=True`**——它带一个可变的运行时缓存字段，
    `frozen` 只锁住"配置"部分（primary / platforms / emoji_enabled）。
    """

    #: 主字体链（用户/主题指定），COMMON 与 SYMBOL 走它。
    primary: tuple[str, ...] = ("sans-serif",)
    #: 三平台候选，按顺序探测。第一条命中的即采用。
    platforms: tuple[_PlatformChain, ...] = (_WINDOWS, _MACOS, _LINUX)
    #: 是否启用 emoji 专用链（关掉则 emoji 也走主字体）。
    emoji_enabled: bool = True

    def __post_init__(self) -> None:
        # 脚本 → 实际可用的族链。不参与相等性比较，所以用普通属性。
        self._cache: dict[FontScript, tuple[str, ...]] = {}

    def chain_for(self, script: FontScript, resolver: FontResolver) -> tuple[str, ...]:
        """给出该脚本实际可用的族链。

        关键点：**先把主字体接在链首**。如果用户指定的字体本身就能画汉字
        （比如用户显式选了"思源黑体"），那它就该赢——回退链只在主字体
        缺字时才起作用。这也是"用户字体"排在 docs/04 那张表最前面的原因。
        """
        hit = self._cache.get(script)
        if hit is not None:
            return hit

        if script in (FontScript.COMMON, FontScript.SYMBOL) or (
            script is FontScript.EMOJI and not self.emoji_enabled
        ):
            chain = resolver_chain(self.primary, resolver, script=script)
        else:
            candidates = self._candidates_for(script)
            chain = resolver_chain(self.primary + candidates, resolver, script=script)

        self._cache[script] = chain
        return chain

    def _candidates_for(self, script: FontScript) -> tuple[str, ...]:
        """按脚本收集三平台候选。探测交给 resolver，这里只负责"候选有哪些"。"""
        is_emoji = script is FontScript.EMOJI
        out: list[str] = []
        for platform in self.platforms:
            source = platform.emoji if is_emoji else platform.cjk
            for family in source:
                if family not in out:
                    out.append(family)
        return tuple(out)


#: 每个脚本的**代表字符**：回退链用它探测"这个族覆盖这个脚本吗"。
#:
#: 只探一个字符就够：字体对某个脚本的覆盖是**整块**来的（有汉字的字体必然有"中"），
#: 而逐字符探测要在每次排版时跑几十次 cmap 查询。COMMON / SYMBOL 不在表里——
#: 拉丁字母与基本符号任何字体都有，不必探测。
_SCRIPT_SAMPLE: dict[FontScript, str] = {
    FontScript.HAN: "中",
    FontScript.KANA: "あ",
    FontScript.HANGUL: "가",
    FontScript.EMOJI: "😀",
}


def _covers(resolver: FontResolver, family: str, script: FontScript | None) -> bool:
    """族存在 **且**（问的是具体脚本时）覆盖该脚本的代表字符。

    只按"族存在"判断回退，会出现"族在、但画不出"：`Segoe UI` 在 Windows 上
    确实存在，但它一个汉字都没有——于是中文 run 会被分配给 Segoe UI，
    渲染成一排豆腐块。**真字体栈下这个问题立刻显形**（内置后端至少还画占位块，
    所以一直没被发现）。这就是 `has_glyph` 存在的全部理由。
    """
    if not resolver.has_family(family):
        return False
    if script is None:
        return True
    sample = _SCRIPT_SAMPLE.get(script)
    if sample is None:
        return True
    return resolver.has_glyph(family, sample)


def resolver_chain(
    desired: tuple[str, ...], resolver: FontResolver, *, script: FontScript | None = None
) -> tuple[str, ...]:
    """过滤出系统里真有的族，保持顺序、去重。

    通用名（`sans-serif` / `serif` / `mono`）永远保留——它们是兜底终点，
    由后端的通用族映射去挑具体字体（最后会落到内嵌兜底字体）。

    `script` 给定时，候选还要**覆盖该脚本的代表字符**才算命中（见 `_covers`）。

    **若结果里一个通用名都没有，末尾补上 `sans-serif`**：
    否则用户给的字体全都没装时链会变空，回退就无路可走，
    上层拿到空链只能自己兜底，那是把责任推给了每个调用点。
    """
    out: list[str] = []
    for family in desired:
        if family in ("sans-serif", "serif", "mono", "system-ui"):
            if family not in out:
                out.append(family)
            continue
        if _covers(resolver, family, script) and family not in out:
            out.append(family)
    if not any(g in out for g in ("sans-serif", "serif", "mono", "system-ui")):
        out.append("sans-serif")
    return tuple(out)


def default_chain(primary: tuple[str, ...]) -> FallbackChain:
    """按主字体链构造默认回退链（三平台候选全带上，用探测结果选）。"""
    return FallbackChain(primary=primary)
