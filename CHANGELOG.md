# 更新日志

本文件格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [未发布]

### 新增
- 项目骨架：`src/inkstone` 分层目录（平台后端 / 渲染 / 文本 / 布局 / 组件树 / 事件 / 无障碍 / 样式 / 组件 / 声明式协议 / 开发者工具）
- **行为原语层 `primitives/`**（8 模块骨架：focus_trap / roving_focus / popper / dismissible / portal / presence / scroll_lock / overlay_manager）——弹层与菜单的专业地基
- **动效引擎 `motion/`**（5 模块骨架：easing / spring / animation / transition / reduced_motion）
- **图标系统 `icons/`**（2 模块骨架：registry / stroke，Lucide 网格规范）
- 工程配置：`pyproject.toml`（hatchling + ruff + mypy strict + pytest）、`Makefile`、`.gitignore`、`.gitattributes`、`LICENSE`(MIT)
- 布局基础类型 `inkstone.layout.types`：`Offset` / `Size` / `Rect` / `EdgeInsets` / `BoxConstraints`（不可变、纯数据、无后端依赖）
- 上述类型的单元测试，验证"无窗口也能测布局"这条原则
- 设计文档：`00-愿景与定位`、`01-架构总览`、`12-架构审查与缺口分析`、`13-设计系统`
- `prototype/`：Phase 0 探针（约 1100 行可运行原型，已验证自绘、中文渲染、简化 flex、无头截图）

### 布局引擎
- `RenderBox` 盒子模型：约束向下、尺寸向上、padding / 溢出诊断 / 基线
- `Row` / `Column`（flex：sizing、权重、gap、主轴与交叉轴对齐）
- `Grid`（fixed / fr / auto 轨道 + span）
- `Stack` / `Positioned` / `Align`
- `ScrollView`（向子级派发无限主轴约束，对应铁律 5）

### 组件树与样式
- 三棵树：Widget / Element / RenderObject，配 `BuildOwner` 帧调度（build / layout / paint 三阶段）
- 环境服务：`context.theme` 与 `context.text_engine`
- 三层令牌 + 明暗主题 + 五层确定性样式解析（`style/resolve.py`）
- 组件：`Box` / `Card` / **`Text`** / `Row` / `Column` / `Flexible` / `Button` / `Input`
- Button 变体配方：6 变体 × 5 尺寸 × 8 状态

### 渲染管线
- 显示列表（指令集：矩形 / 圆角 / 描边 / 裁剪 / **文本 `TextRunOp`**），不可变、可回放、可逐指令比对
- 软件光栅：扫描线 + 水平解析覆盖的抗锯齿，纯 Python、跨平台逐字节确定
- PNG 编码（stdlib 手写，不引入 Pillow）
- **字形两套来源**：内置确定性字形（跨平台一致，CI 与黄金图用）与 **Windows 真字体引擎**
  （`GdiFontEngine`，中文渲染成真正的汉字）；`devtools` 自动配对字形源与度量源
- 确定性截图 + 黄金图基线（7 张，三平台逐字节一致）

### 文本与字体
- `MetricsProvider` 字体度量契约（全库唯一度量入口）
- `text/`：字体解析 / 注册、**三平台 CJK 与 emoji 回退链**、按脚本分段整形、
  UAX #14 核心断行 + **完整 CJK 标点禁则**、段落排版（对齐 / 行高 / `max_lines` /
  省略号 / **命中测试** / **选区几何**）
- 字素簇切分（UAX #29 简化版）：断行、选区、省略号按簇操作，不切半个 emoji

### 示例与工具
- `examples/hello.py`：可运行示例（`--dark` / `--deterministic`），带冒烟测试

### 测试与 CI
- 567 个无头单测，覆盖率 89%
- **架构约束测试**：分层单向依赖、`text/` 层无平台 API、组件零硬编码颜色
- 三平台 CI（Windows / macOS / Ubuntu + 最低支持版本 py3.10）：lint、测试、
  覆盖率门禁、性能预算、架构约束五个任务
- **性能断言与覆盖率刻意分任务**：覆盖率插桩会让性能测试失真，混跑只会产生假信号

### 说明
- 当前版本 `0.1.0.dev0`，公开 API 尚未稳定，任何接口都可能变更。
- `prototype/` 不参与打包与安装，仅作为风险验证记录保留。
- **已知边界**：真字形目前只有 Windows 一份；彩色 emoji 会退化成单色轮廓；
  中文输入（`events/` 事件系统 + 编辑模型）与 DPI 缩放尚未实现。

## 未来版本的记录方式

- `0.x` 阶段：允许破坏性变更，但必须在 CHANGELOG 的 "### 变更" 小节写明迁移方式
- `1.0` 之后：破坏性变更必须先经过一个次版本的废弃警告期
