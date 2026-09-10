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
        "headless 后端为 `present(display_list, clear_color)` 需要 gfx 的"
        "DisplayList 与 Color 类型。这是**接口类型**依赖而非实现依赖，"
        "但方向确实反了。待消除：把 DisplayList/Color 下移到 L0 "
        "或定义一个 L0 侧的呈现描述协议。"
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
    """铁律 2：组件里不许出现字面量颜色。"""

    def test_widgets_contain_no_hex_colors(self) -> None:
        """`widgets/` 里出现 `#RRGGBB` = 审查打回，一律走主题令牌。"""
        import re

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
