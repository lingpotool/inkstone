"""架构约束测试 —— 把"分层单向依赖"从口号变成会失败的测试。

铁律 1（AGENT.md）要求：`L0 平台 → L1 输入/a11y → L2 渲染 → L3 文本 →
L4 布局 → L5 组件树 → L6 样式 → L7 组件 → L8 应用`，**依赖只能向下**。

为什么值得为它写测试：

    分层规则如果只写在文档里，它会在第一次"就 import 一下很快"时破掉，
    而且破掉时不会有任何反馈——等到换平台才发现某个上层模块偷偷摸了
    平台 API，那时改动成本已经翻了几倍。
    这个测试把规则变成 CI 里的一条红线：**新增反向依赖立刻红**。

关于既存例外：
    扫描发现仓库里有若干历史遗留的反向依赖（见 `KNOWN_EXCEPTIONS`）。
    这里**不掩盖**它们，也不顺手"修掉"（那属于未授权的重构），
    而是**显式登记**：每一处都要写清为什么、什么时候该消除。
    一旦有人消除了某处，测试会因为"登记表里有失效条目"而提示删掉它，
    登记表因此不会腐化成"什么都能过"的垃圾桶。
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

# 分层表。数值越小越底层。0.5 步进用于"次级层"（如 L6.5 primitives）。
LAYERS: dict[str, float] = {
    "backend": 0,
    "events": 1,
    "a11y": 1,
    "gfx": 2,
    "icons": 2,
    "text": 3,
    "layout": 4,
    "motion": 4.5,
    "core": 5,
    "style": 6,
    "primitives": 6.5,
    "widgets": 7,
    "app": 8,
    "cli": 8,
    "spec": 8,
    "devtools": 9,
}

# 既存的反向依赖。键是 (来源包, 目标包)，值是"为什么暂时允许"。
# 新增条目必须在这里留下理由，否则测试会拦下来。
KNOWN_EXCEPTIONS: dict[tuple[str, str], str] = {
    ("gfx", "layout"): (
        "几何原语例外：layout/types.py（Rect/Size/Offset/EdgeInsets）实为"
        "全库共享的哑数据内核，不含任何布局算法。按 docs/01 的精神，"
        "几何原语可视为 L-1，gfx 与 text 都可使用。"
        "待消除：把 types 抽到独立包（会影响 164 个布局测试的 import，"
        "需单独一次重构提交）。"
    ),
    ("text", "layout"): (
        "同上（text/paragraph.py 用 Rect 描述选区矩形）。消除方式与上一条一致：抽独立几何包。"
    ),
    ("backend", "gfx"): (
        "R5.8 消除了 headless present() 那条；现存的是 hbft_fonts.py 的"
        "`mask_for` 返回 gfx 的 GlyphMask 类型（TYPE_CHECKING + 延迟导入）。"
        "这是**接口类型**依赖而非实现依赖。待消除：把 GlyphMask 下移到 L0 "
        "（跟随几何原语抽独立包的那次重构一起做）。"
    ),
    ("core", "style"): (
        "core/element.py 与 binding.py 引用了 style 的类型。"
        "待核查：若只是类型注解，应改为 TYPE_CHECKING 导入或下移；"
        "若是运行期依赖，需要引入「样式提供者」协议解耦。"
    ),
}

_LAYER_NOTE = "L{src} 依赖了 L{dst}（上层依赖下层）"


def _iter_source_modules() -> list[tuple[str, ast.Module, pathlib.Path]]:
    """列出 `src/inkstone/` 下所有分层包内模块的 (顶层包名, AST, 路径)。"""
    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "inkstone"
    out: list[tuple[str, ast.Module, pathlib.Path]] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if len(rel.parts) < 2:  # 包根文件（__init__.py / app.py）不算分层内
            continue
        top = rel.parts[0]
        if top not in LAYERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        out.append((top, tree, path))
    return out


def _resolve_relative_module(path: pathlib.Path, node: ast.ImportFrom) -> str | None:
    """把相对 import 解析成 `a.b.c` 形式的绝对模块名。"""
    if node.level == 0 or not node.module:
        return None
    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "inkstone"
    rel = path.relative_to(root)
    base = list(rel.parts[:-1])  # 去掉文件名，留包路径
    up = node.level - 1
    if up > 0:
        base = base[:-up] if up <= len(base) else []
    parts = base + node.module.split(".")
    return ".".join(parts)


def _collect_violations() -> dict[tuple[str, str], list[str]]:
    """扫描出所有"上层依赖下层"的 (来源包, 目标包) 组合及具体文件。"""
    violations: dict[tuple[str, str], list[str]] = {}
    for top, tree, path in _iter_source_modules():
        root = pathlib.Path(__file__).resolve().parents[2] / "src" / "inkstone"
        rel = path.relative_to(root)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = _resolve_relative_module(path, node)
            if module is None:
                continue
            parts = module.split(".")
            if parts and parts[0] == "inkstone":
                parts = parts[1:]
            if not parts:
                continue
            target = parts[0]
            if target not in LAYERS or target == top:
                continue
            if LAYERS[target] > LAYERS[top]:
                violations.setdefault((top, target), []).append(str(rel))
    return violations


class TestLayerDependencies:
    """铁律 1：依赖只能向下。"""

    def test_no_undeclared_reverse_dependencies(self) -> None:
        """任何**未登记**的反向依赖都要让 CI 红。"""
        violations = _collect_violations()
        undeclared = {k: v for k, v in violations.items() if k not in KNOWN_EXCEPTIONS}
        assert not undeclared, (
            "发现未登记的反向依赖（违反铁律 1：依赖只能向下）：\n"
            + "\n".join(
                f"  {src} → {dst}: {sorted(set(files))}"
                f"\n      {_LAYER_NOTE.format(src=LAYERS[src], dst=LAYERS[dst])}"
                for (src, dst), files in sorted(undeclared.items())
            )
            + "\n\n修法：把依赖下移，或（若确属共享哑类型）在 "
            "KNOWN_EXCEPTIONS 里登记并写明理由。"
        )

    def test_exception_registry_has_no_stale_entries(self) -> None:
        """登记表里不能有"已经不成问题了"的条目——否则它会腐化成垃圾桶。

        这条保证例外是**临时的**：一旦某处反向依赖被消除，
        测试会提醒把它从登记表里删掉，规则不会随时间被稀释。
        """
        violations = _collect_violations()
        stale = [k for k in KNOWN_EXCEPTIONS if k not in violations]
        assert not stale, "以下反向依赖已不存在，请从 KNOWN_EXCEPTIONS 中删除：\n" + "\n".join(
            f"  {src} → {dst}" for src, dst in sorted(stale)
        )

    def test_every_exception_documents_its_reason(self) -> None:
        """每条例外都必须写清理由与消除方式，不许留空。"""
        for key, reason in KNOWN_EXCEPTIONS.items():
            assert reason.strip(), f"例外 {key} 没有写理由"
            assert len(reason) > 20, f"例外 {key} 的理由太简短，无法说明问题"

    def test_text_layer_is_platform_free(self) -> None:
        """**L3 文本层不许碰平台 API。**

        docs/04 §3 的"度量同源"在结构上的保证：`text/` 只能通过
        `backend` 的协议问度量，绝不自己调系统 API。
        一旦这里破掉，"两套度量"就会立刻出现，表现是文字截断与基线漂移。

        判定走 AST，只看**真实的 import 与标识符引用**——
        文档与注释里提到 "fontconfig"（比如引用 docs/04 那张平台链表）
        是正常的，不该被误报。
        """
        root = pathlib.Path(__file__).resolve().parents[2] / "src" / "inkstone" / "text"
        banned_modules = {"ctypes", "cffi", "CoreText", "fontconfig", "freetype", "uiautomation"}
        banned_names = {"windll", "cdll", "oledll", "WinDLL", "PyDLL"}
        offenders: list[str] = []

        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".")[0] in banned_modules:
                            offenders.append(f"{path.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    module = (node.module or "").split(".")[0]
                    if module in banned_modules:
                        offenders.append(f"{path.name}: from {node.module} import ...")
                elif isinstance(node, ast.Name) and node.id in banned_names:
                    offenders.append(f"{path.name}: 引用 {node.id}")
                elif isinstance(node, ast.Attribute) and node.attr in banned_names:
                    offenders.append(f"{path.name}: 引用 .{node.attr}")

        assert not offenders, (
            "text/ 层出现了平台 API（违反 docs/04 §3 度量同源）：\n"
            + "\n".join(f"  {o}" for o in offenders)
            + "\n\n修法：把平台调用下沉到 backend/，text/ 只面向 MetricsProvider 协议。"
        )

    def test_text_layer_does_not_import_core_or_widgets(self) -> None:
        """L3 不许反向依赖 L5 组件树 / L7 组件。"""
        root = pathlib.Path(__file__).resolve().parents[2] / "src" / "inkstone" / "text"
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                module = _resolve_relative_module(path, node)
                if module is None:
                    continue
                parts = module.split(".")
                if parts and parts[0] == "inkstone":
                    parts = parts[1:]
                if parts and parts[0] in ("core", "widgets", "style"):
                    pytest.fail(
                        f"{path.name} 反向依赖了 {parts[0]}（违反铁律 1）。"
                        f"text 是 L3，只能向下依赖 backend/gfx/layout。"
                    )


class TestNoHardcodedLiterals:
    """铁律 2：组件里不许出现字面量。"""

    def test_widgets_contain_no_hex_colors(self) -> None:
        """`widgets/` 里出现 `#RRGGBB` = 审查打回，一律走主题令牌。"""
        root = pathlib.Path(__file__).resolve().parents[2] / "src" / "inkstone" / "widgets"
        pattern = re.compile(r"#[0-9a-fA-F]{6}\b")
        offenders: list[str] = []
        for path in sorted(root.rglob("*.py")):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{path.name}:{lineno}: {line.strip()}")
        assert not offenders, "组件里出现硬编码颜色（违反铁律 2）：\n" + "\n".join(
            f"  {o}" for o in offenders
        )

    def test_widgets_have_no_hardcoded_design_values(self) -> None:
        """`widgets/` 里的圆角 / 描边宽度 / 控件高度不许写字面量。

        颜色扫描（上一条）只扫 `#RRGGBB`，于是"圆角回退 10.0""描边写死 1.0"
        "控件高度默认 36.0"这类**数值**硬编码全部漏网（docs/16 §R2.4 的实锤）。
        这条把扫描扩到数值，但只覆盖三类设计槽位——**窄扫描，宁可漏不可吵**：
        一个误报多的扫描会被人习惯性忽略，那比没有更糟。
        """
        root = pathlib.Path(__file__).resolve().parents[2] / "src" / "inkstone" / "widgets"
        offenders: list[str] = []
        for path in sorted(root.rglob("*.py")):
            offenders.extend(
                _design_literal_violations(path.read_text(encoding="utf-8"), path.name)
            )
        assert not offenders, (
            "组件里出现硬编码的设计数值（违反铁律 2）：\n"
            + "\n".join(f"  {o}" for o in offenders)
            + "\n\n修法：先看 style/tokens.py 有没有对应令牌，没有就加一个再用。"
            "\n（0 / 0.0 视为中性值，不算设计值；比值类请命名为 `*_ratio`。）"
        )


# 三类"设计槽位"：值必须来自 style/tokens.py，不许写字面量。
#
# 名字匹配刻意**窄**：只认这三类，且要求名字以槽位词结尾（`height_ratio` 不是槽位，
# 它是比值；`item_height` 是）。窄扫描的漏网由代码审查兜，误报却会让门禁被忽略。
_DESIGN_SLOT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("圆角", re.compile(r"^(?:.*_)?(?:radius|corner)$")),
    ("描边宽度", re.compile(r"^(?:.*_)?(?:border_width|stroke_width)$")),
    ("控件高度", re.compile(r"^(?:.*_)?(?:height|height_value)$")),
)


def _slot_category(name: str) -> str | None:
    for category, pattern in _DESIGN_SLOT_PATTERNS:
        if pattern.match(name):
            return category
    return None


def _slot_name(target: ast.expr) -> str | None:
    """赋值目标的"名字"：`self.radius` → `radius`，`radius` → `radius`。"""
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _design_literal_violations(source: str, filename: str = "<memory>") -> list[str]:
    """在源码里找出"设计槽位被写成字面量"的地方。

    看三类位置：**赋值**（含带注解赋值）、**关键字实参**、以及**绘制原语的位置实参**。

    为什么要专门管位置实参：审查抓到的漏网之一是 `stroke(rect, 1.0, ...)`——
    描边宽度写死在调用点，连名字都没有，光看赋值是抓不到的。
    光栅录制器的签名是固定的（`gfx/paint.py`），所以这里能精确到位置，不必猜。
    `2.0 * self.padding_h` 这类系数不在任何槽位上，不会被误判。
    """
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword):
            _check_slot(node.arg, node.value, node.lineno, filename, offenders)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                _check_slot(_slot_name(target), node.value, node.lineno, filename, offenders)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            _check_slot(_slot_name(node.target), node.value, node.lineno, filename, offenders)
        elif isinstance(node, ast.Call):
            _check_positional_slots(node, filename, offenders)
    return offenders


# 绘制原语的"哪个位置参数是设计值"。签名固定（gfx/paint.py）：
#     stroke_rect(rect, width, color, radius=0.0)
#     round_rect(rect, radius, color)
# `stroke` / `round` 是组件里 `getattr(context, "stroke_rect")` 之后的别名。
_DRAW_PRIMITIVE_SLOTS: dict[str, dict[int, str]] = {
    "stroke_rect": {1: "stroke_width", 3: "radius"},
    "stroke": {1: "stroke_width", 3: "radius"},
    "round_rect": {1: "radius"},
    "round": {1: "radius"},
}


def _callee_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _check_positional_slots(call: ast.Call, filename: str, offenders: list[str]) -> None:
    slots = _DRAW_PRIMITIVE_SLOTS.get(_callee_name(call.func) or "")
    if slots is None:
        return
    for index, slot_name in slots.items():
        if index < len(call.args):
            _check_slot(slot_name, call.args[index], call.lineno, filename, offenders)


def _numeric_literals(node: ast.expr) -> list[float]:
    """子树里所有**非零**数值字面量。

    为什么要钻整棵子树而不是只看顶层：审查实锤的第一个漏网写成
    `radius = self.radius or 10.0`——顶层是 `BoolOp`，只看顶层会漏掉那个 10.0。
    而"给槽位赋的值里出现设计数字"正是要拦的东西。
    0 / 0.0 是中性值（"未配置 / 无"），不算设计决定，放行。
    """
    out: list[float] = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Constant):
            continue
        if isinstance(sub.value, bool) or not isinstance(sub.value, (int, float)):
            continue
        value = float(sub.value)
        if value != 0.0:
            out.append(value)
    return out


def _check_slot(
    name: str | None,
    value: ast.expr,
    lineno: int,
    filename: str,
    offenders: list[str],
) -> None:
    if name is None:
        return
    category = _slot_category(name)
    if category is None:
        return
    for literal in _numeric_literals(value):
        offenders.append(
            f"{filename}:{lineno}: {category} {name} 里出现字面量 {literal:g}"
            f"（应当来自 style/tokens.py）"
        )


class TestNumericLiteralScanSelfCheck:
    """自验证：扫描本身要能抓到硬编码（docs/16 §R2.4 的验收）。

    门禁最常见的死法是"扫描写错了 → 永远绿灯"，所以这里故意喂几段
    该红与该绿的代码，把扫描的行为钉死。
    """

    @pytest.mark.parametrize(
        "source",
        [
            "self.radius = 10.0\n",
            "render_object.border_width = 1.0\n",
            "self.height_value = 36.0\n",
            "box = Box(height=52.0)\n",
            "self.corner = 6.0\n",
            "self.item_height = 24.0\n",
            "radius = self.radius or 10.0\n",  # 审查实锤：字面量藏在布尔表达式里
            "stroke(rect, 1.0, color)\n",  # 位置实参也要抓（审查实锤的漏网形态）
            "context.stroke_rect(rect, 2.0, color)\n",
            "round_rect(rect, 10.0, color)\n",
        ],
    )
    def test_hardcoded_slot_is_flagged(self, source: str) -> None:
        assert _design_literal_violations(source), f"没抓到硬编码：{source!r}"

    @pytest.mark.parametrize(
        "source",
        [
            "self.radius = 0.0\n",  # 中性值
            "self.border_width: float = 0.0\n",
            "self.radius = theme.radius('md')\n",  # 来自令牌
            "height_ratio = 1.0\n",  # 比值，不是槽位
            "padding = 12.0\n",  # 不在三类槽位里（间距由组件层从令牌取）
            "width = 2.0 * self.padding_h\n",  # 系数
            "self.radius = other.radius\n",
            "stroke(rect, self.border_width, color)\n",  # 位置对了但值来自字段
            "helper(1.0, 2.0)\n",  # 不是绘制原语
        ],
    )
    def test_non_violation_is_not_flagged(self, source: str) -> None:
        assert _design_literal_violations(source) == [], f"误报了：{source!r}"
