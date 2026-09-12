"""字体库（L0a）—— 字体文件的**发现、加载、解析、匹配**。

这是 R4 的地基：把"这台机器上有哪些字体、每个族有哪些字重/斜体、
它们分别在哪个文件的第几个子面里"这件事做成一份**可查询的索引**。
上层（`text/fallback.py`、排版、组件）只问"有没有这个族""帮我挑一个最接近的"，
不需要知道字体目录在哪、TTC 是什么。

## 为什么在 L0

字体**文件的位置**是平台知识（Windows 的 `C:/Windows/Fonts`、macOS 的
`/System/Library/Fonts`、Linux 的 fontconfig 路径），而铁律 1 要求
"平台差异不出 L0"。所以路径知识关在这里，`text/` 层一行都不碰。

## 为什么用 nameID 16 而不是 nameID 1

同一个族在系统里常常拆成多个文件（`msyh.ttc` 里就有 YaHei 与 YaHei UI），
而 **nameID 1（legacy family）会把 "YaHei Light" 也算成 "YaHei"**——
按 nameID 1 建索引，`family="Microsoft YaHei Light"` 永远找不到，
而 `family="Microsoft YaHei"` 会撞上一堆字重不同的面。
nameID 16（typographic family）才是设计意图上的族名，**优先用它**，
没有（老字体）才退回 nameID 1。

## 为什么要按 CSS 的字重匹配规则挑

"请求 BOLD，系统只有 Regular 与 Black"这种情形必须有**确定**的答案。
随便挑一个的后果是：`font-weight: 700` 的标题可能渲染成 Black，
看起来像"字重选错了"，而没有任何报错。CSS Fonts 4 §5.2.2 定义了优先级，
照抄它——它是被四个浏览器与几十亿页面验证过的规则。

## 错误处理

扫描时遇到损坏的字体文件**跳过但记账**（`skipped`）：系统字体目录里有 300 个
文件，其中一个坏了不该让整个应用起不来；但"跳过了什么"必须能查，
不然就成了静默失败。查询时的错误（族不存在、字号非法）走 `FontMetricsError`。

状态：已实现（R4.1）。
"""

from __future__ import annotations

import contextlib
import logging
import os
import pathlib
import sys
import warnings
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field

from .fonts import FontMetricsError, FontSlant, FontWeight

__all__ = [
    "DEFAULT_FALLBACK_FAMILY",
    "FontLibrary",
    "FontRecord",
    "default_font_directories",
    "default_font_library",
    "embedded_font_path",
    "generic_candidates",
    "order_weights",
]

#: 内嵌兜底字体的族名（R4.4 注册）。回退链的**终点**：
#: 系统上什么都没有时，中文也不能出豆腐块——这是结构保证，不是赌运气。
DEFAULT_FALLBACK_FAMILY = "Inkstone Sans"

#: 通用族名 → 各平台的默认族（按优先级）。
#: 这是**尽力而为**的映射：系统上没装就继续往后找，最后落到内嵌兜底字体。
#: macOS / Linux 的具体族名没法在本机验证，所以它们只是"先试试"的候选，
#: 真正的保证在内嵌字体那一层。
_GENERIC_CANDIDATES: dict[str, dict[str, tuple[str, ...]]] = {
    "win32": {
        "system-ui": ("Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"),
        "sans-serif": ("Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "Arial"),
        "serif": ("Times New Roman", "SimSun", "Cambria"),
        "monospace": ("Consolas", "Cascadia Mono", "Courier New"),
    },
    "darwin": {
        "system-ui": ("SF Pro Text", "Helvetica Neue", "PingFang SC"),
        "sans-serif": ("Helvetica Neue", "PingFang SC", "Arial"),
        "serif": ("Times New Roman", "Songti SC"),
        "monospace": ("SF Mono", "Menlo", "Courier New"),
    },
    "linux": {
        "system-ui": ("Noto Sans", "DejaVu Sans", "Noto Sans CJK SC"),
        "sans-serif": ("DejaVu Sans", "Noto Sans", "Noto Sans CJK SC"),
        "serif": ("DejaVu Serif", "Noto Serif", "Noto Serif CJK SC"),
        "monospace": ("DejaVu Sans Mono", "Noto Sans Mono", "Noto Sans Mono CJK SC"),
    },
}

#: `mono` 是 CSS 之外的习惯写法，与 `monospace` 等价
_GENERIC_ALIASES = {"mono": "monospace", "ui-sans-serif": "sans-serif", "ui-monospace": "monospace"}

#: 族名 → 字重的兜底推断（少数老字体没有 OS/2 表）
_SUBFAMILY_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("thin", 100),
    ("extralight", 200),
    ("ultralight", 200),
    ("light", 300),
    ("regular", 400),
    ("normal", 400),
    ("book", 400),
    ("medium", 500),
    ("semibold", 600),
    ("demibold", 600),
    ("bold", 700),
    ("extrabold", 800),
    ("ultrabold", 800),
    ("black", 900),
    ("heavy", 900),
)

#: `FontWeight` 枚举 → OpenType `usWeightClass`
WEIGHT_VALUES: dict[FontWeight, int] = {
    FontWeight.REGULAR: 400,
    FontWeight.MEDIUM: 500,
    FontWeight.SEMIBOLD: 600,
    FontWeight.BOLD: 700,
}


@dataclass(frozen=True, slots=True)
class FontRecord:
    """索引里的一条：一个**具体字体面**。

    一个族在索引里对应多条记录（Regular / Bold / Italic …），
    由 `find()` 按 CSS 规则挑出最合适的那一条。
    """

    family: str
    path: str
    #: TTC / OTC 里的子面序号；单字体文件恒为 0
    index: int = 0
    #: OpenType `usWeightClass`（100–900）
    weight: int = 400
    slant: FontSlant = FontSlant.NORMAL
    #: 子族名（"Bold Italic" 之类），只用于诊断与检查器展示
    subfamily: str = ""
    #: 是否是内嵌兜底字体
    is_fallback: bool = False

    def describe(self) -> str:
        parts = [self.family]
        if self.subfamily:
            parts.append(self.subfamily)
        if self.index:
            parts.append(f"#{self.index}")
        return " ".join(parts)


def order_weights(available: Iterable[int], target: int) -> list[int]:
    """按 **CSS Fonts 4 §5.2.2** 的优先级排列可用的字重。

    规则（照抄规范，别自己发明）：

    - `target` 在 400–500：先看 `[target, 500]` 升序，再 `< target` 降序，
      最后 `> 500` 升序；
    - `target < 400`：先 `≤ target` 降序，再 `> target` 升序；
    - `target > 500`：先 `≥ target` 升序，再 `< target` 降序。

    为什么要照抄：请求 BOLD 而系统只有 Regular 与 Black 时，
    "挑哪个"必须有确定答案。随便挑的后果是标题渲染成 Black 却毫无报错。
    """
    values = sorted(set(available))
    if target < 400:
        return sorted((w for w in values if w <= target), reverse=True) + [
            w for w in values if w > target
        ]
    if target > 500:
        return [w for w in values if w >= target] + sorted(
            (w for w in values if w < target), reverse=True
        )
    return (
        [w for w in values if target <= w <= 500]
        + sorted((w for w in values if w < target), reverse=True)
        + [w for w in values if w > 500]
    )


def generic_candidates(family: str) -> tuple[str, ...]:
    """通用族名（`sans-serif` / `system-ui` / `mono` …）在当前平台对应的候选族。

    非通用名原样返回单元素元组——调用方不必先判断"这是不是通用名"。
    """
    key = family.strip()
    canonical = _GENERIC_ALIASES.get(key, key)
    table = _GENERIC_CANDIDATES.get(_platform_key(), {})
    if canonical in table:
        return table[canonical]
    return (family,)


def _is_windows() -> bool:
    """平台判断**包成函数**，而不是到处写 `sys.platform == "win32"`。

    mypy 会按 `sys.platform` 的**字面量**做平台收窄：在 Windows 上跑 mypy 时，
    `if sys.platform == "win32": return ...` 之后的 darwin / linux 分支会被判成
    "不可达"（我们开了 `warn_unreachable`），于是三平台代码只能在**自己**那个
    平台上通过类型检查。这是本仓库踩过的坑（`Makefile type-all` 存在的理由）。
    """
    return sys.platform == "win32"


def _is_macos() -> bool:
    """见 `_is_windows`。"""
    return sys.platform == "darwin"


def _platform_key() -> str:
    if _is_windows():
        return "win32"
    if _is_macos():
        return "darwin"
    return "linux"


def default_font_directories() -> tuple[pathlib.Path, ...]:
    """当前平台的系统字体目录（只返回**存在**的那些）。

    多给几个目录没有代价（不存在就跳过），少给一个的代价是"这个字体明明装了
    却找不到"。用户级目录也要收：Windows 10+ 允许非管理员装字体，
    那些字体进的是 `%LOCALAPPDATA%\\Microsoft\\Windows\\Fonts`。
    """
    home = pathlib.Path.home()
    if _is_windows():
        windir = os.environ.get("WINDIR") or r"C:\Windows"
        local = os.environ.get("LOCALAPPDATA")
        raw = [pathlib.Path(windir) / "Fonts"]
        if local:
            raw.append(pathlib.Path(local) / "Microsoft" / "Windows" / "Fonts")
    elif _is_macos():
        raw = [
            pathlib.Path("/System/Library/Fonts"),
            pathlib.Path("/Library/Fonts"),
            home / "Library" / "Fonts",
        ]
    else:
        data_home = os.environ.get("XDG_DATA_HOME")
        raw = [
            pathlib.Path("/usr/share/fonts"),
            pathlib.Path("/usr/local/share/fonts"),
            pathlib.Path(data_home) / "fonts" if data_home else home / ".local" / "share" / "fonts",
            home / ".fonts",
        ]
    return tuple(p for p in raw if p.is_dir())


#: 会被扫描的字体文件后缀。`.ttc`/`.otc` 是集合（一个文件多个面）。
_FONT_SUFFIXES = (".ttf", ".otf", ".ttc", ".otc")

#: 内嵌兜底字体的文件名（由 `tools/build_embedded_font.py` 产出）
_EMBEDDED_FONT = "InkstoneSans-Regular.otf"


def embedded_font_path() -> pathlib.Path | None:
    """内嵌兜底字体的路径；资源被裁剪掉时返回 `None`。

    返回 `None` 而不是抛异常：字体缺失应当降级为"回退到系统默认"（还能跑），
    而不是让 `import` 就失败。但**"缺了"必须能查**——
    `FontLibrary.stats()["embedded"]` 会如实报 0/1。
    """
    path = pathlib.Path(__file__).with_name("fonts_data") / _EMBEDDED_FONT
    return path if path.is_file() else None


@dataclass
class _FaceInfo:
    """从一个字体文件里读出来的单个面。"""

    family: str
    subfamily: str
    weight: int
    slant: FontSlant


def _infer_weight(subfamily: str) -> int:
    """从子族名猜字重。只在没有 OS/2 表时用。"""
    lowered = subfamily.lower().replace(" ", "")
    for token, weight in _SUBFAMILY_WEIGHTS:
        if token in lowered:
            return weight
    return 400


def _read_faces(path: pathlib.Path) -> list[tuple[int, _FaceInfo]]:
    """读出一个字体文件里所有面的 `(真实子面序号, 信息)`。

    **序号必须是文件里的真实下标**，不能是"过滤掉竖排族之后的位置"：
    TTC 的子面是按物理顺序寻址的（`freetype.Face(path, index=i)`），
    序号错一位就会加载到同文件里**另一个字体**——表现是"选 YaHei 出来的是
    YaHei Light"，而且不报错。所以过滤 `None` 时要连下标一起保留。

    **只读 name / OS/2 / head 三张表**（`lazy=True`）：扫描 300 个系统字体
    是"秒级"操作，代价全在打开文件；真去解析字形表要慢两个数量级，
    而索引阶段一个字形的数据都不需要。

    fontTools 在这里**延迟导入**：`import inkstone` 不该为了一个可能用不到的
    能力付 200ms 的启动开销（无头测试、纯布局场景根本不需要字体索引）。
    """
    from fontTools.ttLib import TTCollection, TTFont

    def from_font(font: object) -> _FaceInfo | None:
        name = font["name"]  # type: ignore[index]
        family = name.getDebugName(16) or name.getDebugName(1)
        subfamily = name.getDebugName(17) or name.getDebugName(2) or ""
        if not family or family.startswith("@"):
            # "@" 前缀是 Windows 竖排 CJK 变体族，不是给横向排版用的
            return None
        weight = 400
        slant = FontSlant.NORMAL
        has_os2 = "OS/2" in font  # type: ignore[operator]
        if has_os2:
            os2 = font["OS/2"]  # type: ignore[index]
            weight = int(getattr(os2, "usWeightClass", 400) or 400)
            if getattr(os2, "fsSelection", 0) & 0x01:
                slant = FontSlant.ITALIC
        else:
            weight = _infer_weight(subfamily)
        if "head" in font and getattr(font["head"], "macStyle", 0) & 0x02:  # type: ignore[operator,index]
            slant = FontSlant.ITALIC
        if "italic" in subfamily.lower() or "oblique" in subfamily.lower():
            slant = FontSlant.ITALIC
        return _FaceInfo(family=family, subfamily=subfamily, weight=weight, slant=slant)

    if path.suffix.lower() in (".ttc", ".otc"):
        collection = TTCollection(str(path), lazy=True)
        out: list[tuple[int, _FaceInfo]] = []
        for face_index, font in enumerate(collection.fonts):
            info = from_font(font)
            if info is not None:
                out.append((face_index, info))
        return out
    info = from_font(TTFont(str(path), lazy=True))
    return [] if info is None else [(0, info)]


@contextlib.contextmanager
def _quiet_fonttools() -> Iterator[None]:
    """读字体期间静音 fontTools 的噪声日志。

    系统字体目录里有一批老字体带畸形的时间戳，fontTools 会为它们逐个打
    `log.warning`（"created timestamp seems very low…"）。那不是我们的问题，
    但它会**淹没我们自己的告警**——所以只在这一段提高阈值，读完立刻还原，
    不去改全局日志配置（改全局是库最不该干的事之一）。
    """
    logger = logging.getLogger("fontTools")
    previous = logger.level
    logger.setLevel(logging.ERROR)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            yield
    finally:
        logger.setLevel(previous)


@dataclass
class FontLibrary:
    """字体索引：族名 + 字重 + 斜体 → 具体字体面。

    索引**只在第一次查询时建**（`scan()` 是幂等的）。启动成本是一次目录遍历
    加每文件三张表的读取——比"每次排版都去问系统"便宜得多，也比
    "启动时解析全部字形"便宜两个数量级。
    """

    #: 要扫描的目录。`None` = 用当前平台的默认目录；**空元组 = 一个都不扫**。
    #: 两者必须能区分：`FontLibrary(directories=())` 的意图是"空库"（测试与
    #: 只注册用户字体的场景），若把它当成"没指定"去扫全系统，
    #: 就会得到一个悄悄多出 300 个族的库——那种惊喜正是本项目最讨厌的。
    directories: tuple[pathlib.Path, ...] | None = None
    #: 是否注册内嵌兜底字体（默认注册）。传 `False` 得到"一个字体都没有"的库——
    #: 测试要隔离，或者要诊断"没有兜底字体时会怎样"。
    embedded: bool = True
    #: 扫描时跳过的文件（损坏 / 读不了）。**必须能查**，否则就是静默失败。
    skipped: list[tuple[str, str]] = field(default_factory=list)

    _families: dict[str, list[FontRecord]] = field(default_factory=dict, repr=False)
    _fallback: FontRecord | None = field(default=None, repr=False)
    _scanned: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        if self.directories is None:
            self.directories = default_font_directories()
        if self.embedded:
            self._register_embedded()

    def _register_embedded(self) -> None:
        """注册内嵌兜底字体。它是回退链的**终点**（docs/18 §R4.4）。

        失败会抛 `FontMetricsError` 而不是被吞掉：打包漏了资源文件、
        或者资源被截断，都是**构建问题**，必须当场看见——
        静默降级会让"中文在精简环境里变豆腐块"变成一个要查半天的问题。
        """
        path = embedded_font_path()
        if path is not None:
            self.register_file(path, is_fallback=True)

    @property
    def _dirs(self) -> tuple[pathlib.Path, ...]:
        """`directories` 在 `__post_init__` 之后一定非 None；mypy 不知道这件事。

        与其在三个调用点写 `assert`，不如收成一个带断言的属性——
        不变量只在一处声明，读代码的人也只在一处看到它。
        """
        assert self.directories is not None
        return self.directories

    # ------------------------------------------------------------ 建索引

    def scan(self) -> None:
        """扫描字体目录建索引。幂等——重复调用只扫一次。"""
        if self._scanned:
            return
        self._scanned = True  # 先置位：扫描过程中 register 进来的记录不该被清掉
        for directory in self._dirs:
            self._scan_directory(directory)

    def _scan_directory(self, directory: pathlib.Path) -> None:
        try:
            entries = sorted(directory.iterdir())
        except OSError as exc:
            self.skipped.append((str(directory), f"目录读不了：{exc}"))
            return
        for entry in entries:
            if entry.suffix.lower() in _FONT_SUFFIXES and entry.is_file():
                self._index_file(entry)

    def _index_file(self, path: pathlib.Path, *, is_fallback: bool = False) -> None:
        try:
            with _quiet_fonttools():
                faces = _read_faces(path)
        except Exception as exc:
            self.skipped.append((str(path), f"{type(exc).__name__}: {exc}"))
            return
        for face_index, face in faces:
            record = FontRecord(
                family=face.family,
                path=str(path),
                index=face_index,
                weight=face.weight,
                slant=face.slant,
                subfamily=face.subfamily,
                is_fallback=is_fallback,
            )
            self._families.setdefault(face.family, []).append(record)
            if is_fallback and self._fallback is None:
                self._fallback = record

    def register_file(
        self, path: str | os.PathLike[str], *, is_fallback: bool = False
    ) -> tuple[FontRecord, ...]:
        """注册一个字体文件（CSS `@font-face` 的等价物）。

        用户自带字体走这里，而不是把文件塞进系统字体目录——
        应用不该为了用一个字体去改用户机器的全局状态。
        `is_fallback=True` 时它成为回退链的终点（内嵌兜底字体用这个入口）。
        """
        target = pathlib.Path(path)
        if not target.is_file():
            raise FontMetricsError(f"字体文件不存在：{target}")
        self._index_file(target, is_fallback=is_fallback)
        added = tuple(
            r for records in self._families.values() for r in records if r.path == str(target)
        )
        if not added:
            reason = next((why for where, why in self.skipped if where == str(target)), "未知原因")
            raise FontMetricsError(f"字体文件读不出任何字体面：{target}（{reason}）")
        return added

    # ------------------------------------------------------------ 查询

    def has_family(self, family: str) -> bool:
        self.scan()
        return any(name in self._families for name in self._candidates_for(family))

    def families(self) -> tuple[str, ...]:
        """索引里所有族名（排序）。检查器与诊断用。"""
        self.scan()
        return tuple(sorted(self._families))

    def find(
        self,
        family: str,
        *,
        weight: FontWeight = FontWeight.REGULAR,
        slant: FontSlant = FontSlant.NORMAL,
    ) -> FontRecord | None:
        """在**一个**族里挑最合适的那个面。找不到返回 `None`（由调用方决定兜底）。"""
        self.scan()
        target = WEIGHT_VALUES.get(weight, 400)
        for name in self._candidates_for(family):
            records = self._families.get(name)
            if records:
                return _best_match(records, target, slant)
        return None

    def resolve(
        self,
        families: Sequence[str],
        *,
        weight: FontWeight = FontWeight.REGULAR,
        slant: FontSlant = FontSlant.NORMAL,
    ) -> FontRecord:
        """沿回退链挑第一个命中的族；全都没命中时用内嵌兜底字体。

        兜底字体缺席（R4.4 之前）时抛 `FontMetricsError`——**响亮失败**，
        不返回一个"随便挑的"字体。理由：静默挑错字体的表现是"中文变成方块"
        或"字距全乱"，而报错能让人一眼看出是环境没准备好。
        """
        for family in families:
            hit = self.find(family, weight=weight, slant=slant)
            if hit is not None:
                return hit
        if self._fallback is not None:
            return self._fallback
        raise FontMetricsError(
            f"回退链里一个族都没找到：{list(families)}。"
            f"请检查字体是否安装，或注册内嵌兜底字体（register_file(..., is_fallback=True)）"
        )

    def _candidates_for(self, family: str) -> tuple[str, ...]:
        """把请求的族名展开成"按优先级排列的候选族名"。"""
        name = family.strip()
        if not name:
            raise FontMetricsError("族名不能为空字符串")
        return generic_candidates(name)

    def stats(self) -> dict[str, int]:
        self.scan()
        return {
            "families": len(self._families),
            "faces": sum(len(v) for v in self._families.values()),
            "skipped": len(self.skipped),
            "directories": len(self._dirs),
            "embedded": 1 if self._fallback is not None else 0,
        }


def _best_match(records: Sequence[FontRecord], target: int, slant: FontSlant) -> FontRecord:
    """按 CSS 的检查顺序挑面：**先斜体后字重**。

    斜体先检查是因为它是"类别"（要斜体却给了正体，用户一眼能看出错了），
    而字重是"程度"（要 Bold 给了 Semibold，多数场景可接受）。
    """
    order = order_weights((r.weight for r in records), target)
    rank = {w: i for i, w in enumerate(order)}
    return min(records, key=lambda r: (0 if r.slant is slant else 1, rank.get(r.weight, len(rank))))


_DEFAULT_LIBRARY: FontLibrary | None = None


def default_font_library() -> FontLibrary:
    """进程级的默认字体库（扫描一次，全进程复用）。

    为什么要单例：扫描是"启动一次"的操作，而每个 `FontSpec` 解析都去扫一遍
    会让排版从毫秒级掉到秒级。要隔离（测试、多套字体配置）就自己
    `FontLibrary(directories=...)` 建一个，别改这个。
    """
    global _DEFAULT_LIBRARY
    if _DEFAULT_LIBRARY is None:
        _DEFAULT_LIBRARY = FontLibrary()
    return _DEFAULT_LIBRARY
