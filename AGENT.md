# AGENT.md

给在这个仓库里干活的 AI / 新贡献者看的。读一遍再动手，能省掉大部分返工。

## 这是什么

**inkstone** —— 专业的跨平台 Python 原生 UI 系统。纯 Python 编写、自绘渲染、
Windows / macOS / Linux 一等公民、中文一等公民。

一句话目标：**让"用 Python 描述界面"成为一件专业、可靠、能发布到三个平台的事。**

## 当前状态（动手前先看这里）

Phase 1 · 地基。真实代码覆盖布局、组件树、样式、渲染、**文本**：

| 模块 | 状态 |
|---|---|
| `layout/types.py` | ✅ 几何原语（不可变） |
| `layout/protocol.py` | ✅ 轴 / 对齐 / Sizing / LayoutError |
| `layout/box.py` | ✅ RenderBox 盒子模型 |
| `layout/flex.py` | ✅ Row / Column |
| `layout/grid.py` | ✅ Grid（fixed / fr / auto 轨道 + span） |
| `layout/stack.py` | ✅ Stack / Positioned / Align |
| `layout/scroll.py` | ✅ ScrollView（向子级派发无限主轴约束） |
| `core/`（key / widget / element / render_object / binding） | ✅ 三棵树 + 帧调度 + `text_engine` 环境服务 |
| `backend/`（base / headless / sdl2 / **fonts** / **headless_fonts** / **gdi_fonts**） | ✅ 平台抽象层 + **字体度量契约**（`MetricsProvider`）+ **Windows 真字体引擎** |
| `gfx/color.py` | ✅ Color（hex 解析、插值、WCAG 对比度） |
| `style/`（tokens / theme / resolve / variants） | ✅ 三层令牌 + 明暗主题 + 变体解析 |
| `text/`（font / fallback / shaping / linebreak / paragraph / engine） | ✅ 字体度量、CJK 回退链、整形、断行（含禁则）、段落排版 |
| `widgets/`（basic / layout / form） | ✅ Box / Card / **Text** / Row / Column / Flexible / Button / Input |
| `gfx/`（display_list / paint / **glyphs** / raster.base / raster.software） | ✅ 显示列表（含 **`TextRunOp`**）+ 录制器 + 软件光栅（含文本）+ PNG |
| `devtools/screenshot.py` | ✅ 确定性截图 + 黄金图基线（7 张）+ **字形源自动配对** |
| `examples/hello.py` | ✅ 可运行示例（`--dark` / `--deterministic`），进 CI 冒烟测试 |
| 其余模块（gfx GL+Skia / events / primitives …） | ⬜ 占位桩 |

567 个无头单测全绿，**黄金图逐字节比对**也跑通。

按 ROADMAP 顺序，Phase 1 剩下：② 自研 GL 后端、中文输入（`events/`）、
DPI 缩放、样板 App。渲染与文本这几块已经能出**看起来像正经软件**的界面。

**字体有两种来源，各司其职（ADR-0007 / ADR-0009）**：

- **确定性字形**（默认）：内置 5×7 位图 + 非拉丁占位块，无字体文件，
  跨平台逐比特一致 → 黄金图与 CI 用这条。
- **系统真字体**（`HeadlessBackend(system_fonts=True)` 或注入
  `GdiFontEngine`）：中文渲染成**真正的汉字** → 真机预览与 App 用这条。

两者都是"度量与字形同源"，区别只在源头。`devtools` 会**自动配对**：
组件树用哪个度量源，光栅就用哪个字形源，配错了会立刻看出来（字形叠字）。
示例：`python examples/hello.py`（真字体）／`--deterministic`（确定性）。

**占位桩长这样**：一段说明用途的 docstring + `__all__: list[str] = []`。
看到这个形态就别指望里面有实现，也别在它上面继续叠代码——先实现它。

## 铁律

这五条不是风格建议，是会被 review 打回的硬约束。

1. **分层单向依赖，禁止反向。**
   `L0 平台 → L1 输入/a11y → L2 渲染 → L3 文本 → L4 布局 → L5 组件树 → L6 样式 → L7 组件 → L8 应用`。
   下层不许 import 上层。`layout` 不许 import `core`。

2. **组件里不许出现字面量。** 颜色 / 字号 / 间距 / 圆角 / 阴影 / 动效时长，
   一律来自 `style/tokens.py`。审查标准是全文搜 `#[0-9a-fA-F]{6}` 与硬编码字号间距为 **0**。
   要新值？先加令牌，再用令牌。

3. **确定性优先于一切。** 同样的输入必须得到逐像素相同的输出。
   渲染不读墙上时钟（时钟由后端注入）、不用随机数、不依赖字典遍历序之外的任何顺序。
   这是"可无头截图自检"和"AI 能稳定生成界面"的前提。

4. **中文是一等公民，不是适配项。** IME 组合态、CJK 字体回退链、标点换行禁则、
   涨红跌绿，都要从第一天按中文需求设计。注释与文档用中文写。

5. **布局错了要报错，不许静默。** "无限约束遇到 fill"这类情况抛 `LayoutError`，
   带上节点路径与修复建议。内容溢出要标记出来给检查器看，不许悄悄裁切。

## 环境

仓库里没有全局依赖，**一律用项目虚拟环境**：

```bash
./.venv/Scripts/python.exe -m pytest tests -q      # Windows
./.venv/Scripts/python.exe -m ruff check src tests
./.venv/Scripts/python.exe -m mypy
```

没有 venv 时重建：

```bash
python -m venv .venv && ./.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

系统 Python 里**没有** pytest，直接用 `python -m pytest` 会失败。

## 提交前必跑

```bash
make check   # = ruff check + ruff format --check + mypy(strict) + pytest
```

四条全绿才能提。mypy 是 **strict 模式**，所有函数必须有完整注解。

### CI（`.github/workflows/ci.yml`）跑五个任务

| 任务 | 内容 | 为什么单独成任务 |
|---|---|---|
| `lint` | ruff format --check + ruff check + mypy | 风格与类型常被绕过，单独可见 |
| `test` | 三平台（py3.12）+ Ubuntu py3.10，`-m "not slow"` | 正确性不该取决于 runner 当时有多忙 |
| `coverage` | `-m "not slow"` + `--cov-fail-under=85` | ROADMAP 要求 ≥ 85% |
| `perf` | `-m slow`，**无插桩** | 覆盖率插桩会让性能断言随机变红，两者必须分开 |
| `architecture` | `test_architecture.py` | 分层被破坏要在 CI 上有指名道姓的红点 |

**踩过的坑（值得记住）**：最初 `test` 任务跑全量测试（含 `slow`），
结果 py3.10 的共享 runner 上性能断言以 2.10ms 对 2.0ms 挂了。
墙上时钟断言天生会抖——正确性任务必须排除它。

黄金图的 CI 行为值得说清：**三平台逐字节相同，已经在 CI 上验证过了**
（`77fed3a` 首次推送即 8 个任务全绿）。无头度量表是纯数据、
软件光栅是纯算术、PNG 编码固定参数，全程不碰系统字体/时钟/随机数。
若哪天出现平台间差异，那是确定性的某条链路破了，正是要立刻知道的事。
（真字形后端上线后会引入平台差异，届时按 `tests/golden/<platform>/` 分目录。）

### 关于 ruff 的一条重要配置

`pyproject.toml` 里永久忽略了 **RUF001 / RUF002 / RUF003**。
这三条会把中文标点（，。（）「」）判成"易混淆的 Unicode 字符"——
对中文项目纯属误报。**不要"顺手"把它们打开**，改完会瞬间冒出 600+ 个假错误。

## 已确立的实现约定

这些是踩过坑之后定下来的，改动前先想清楚：

- **文字宽度不算不估算，只度量。** 一切都经 `Backend.measure_text`（全库唯一入口）。
  组件里写"每字 14px"这类近似会在中英混排、字号变化时悄悄算错，**审查直接打回**。
  `Text` 组件的尺寸必须来自 `context.text_engine`，`text/` 里不许出现平台 API
  （`test_architecture.py` 用 AST 扫这条）。
- **文本一切按字素簇操作，不按码点。** 断行、命中测试、选区、省略号截断、
  脚本分段都必须先过 `grapheme_clusters()`。按码点切的后果是删一个字符删掉
  半张 emoji 脸、光标停到 "é" 中间、家庭 emoji 被切成 5 段。
- **行高来自令牌，不是字体自然行高。** `TextStyle.line_height` 是倍数，
  字体的 `ascent+descent` 只用于基线定位。两者混用会让中文行距忽大忽小。
- **渲染层按 `glyph.x` 画，不自己累加 advance。** 文本层可能为字距、
  两端对齐、标点悬挂而把字形放得比"累加宽度"更远或更近；光栅层若重算位置，
  那些排版决策会被静默丢掉——表现是"排版算对了但画歪了"。
- **字形必须画进 `advance` 给定的宽度里。** 按字号自由决定字形宽度会让
  相邻字形直接叠在一起（半角 advance 只有 0.5em，很容易越界）。
- **`RenderObject` 拿不到 `BuildOwner`，环境服务一律"元素写、渲染对象读"。**
  `theme` / `text_engine` 都是这个套路。另外框架**挂载时只调 `create_render_object`**，
  不调 `update_render_object`——字段必须在 `create_*` 里就填满，
  否则首次布局量到空值（表现为"文字没画出来但也不报错"）。
- **`Perform_layout` 里的无限宽是正常输入，不是错误。** Row 里放 Text 时
  宽度无界，此时应走"不换行"路径，而不是抛异常或死循环。

- **盒模型的 `width` 是 border-box。** `width=fixed(160)` 且 `padding=12` 时总宽就是
  160、内容区 136。把 fixed 解释成内容宽度会让 padding 把盒子撑大——没人想要这个。
- **baseline 对齐必须整组计算。** 参考线是组内最大的 baseline。
  逐个子级单独算会让每个都拿自己当参考线，结果恒为 0，表现为"写了 baseline 但界面毫无变化"。
- **`flex` 子级默认 `fit=TIGHT`。** 说"给它 flex 权重"，意图几乎总是"吃掉分到的份额"。
  要 LOOSE（可小于份额）请显式传。
- **`Sizing.fill()` 只在非 flex 父级下生效。** 在 Flex 主轴上想撑满，用
  `row.add(child, flex=1)`，不是给子级设 fill。
- **浮点余数全给最后一个 flex 子级**，保证重复布局逐位一致。像素吸附是光栅层的事。
- **Stack 的定位子级不参与决定尺寸。** 否则角标会把卡片撑大。
- **Row 与 Column 共用一份实现。** 所有几何先换算到 (main, cross) 抽象轴，
  最后一步才翻译回 (x, y)。写两遍一定会出现"Row 有 bug、Column 没有"。
- **Scroll 给子级无限主轴约束，且"滚动造成的溢出"不算 overflow。**
  内容比视口长是设计意图，报了会把检查器淹没。另外：**滚动偏移变了必须 `mark_needs_layout()`**，
  否则下一帧走缓存，表现是"滚了但界面不动"。
- **Grid 超出容器时报溢出，不按比例压缩轨道。** 把 200px 的列悄悄压成 150px
  是极难排查的 bug。同理，跨多格的子级不参与 auto 轨道定尺寸（宽度算到哪一列没有唯一答案）。
- **绘制脏标记挂在 `layout.RenderBox` 上，不在 `core.RenderObject` 上。**
  布局与绘制是同一个节点的两件事（Flutter 亦如此）。拆成两个类会导致每个布局容器
  都要再配一个组合类。`core.RenderObject` 只是"自定义渲染对象该继承的基类"。
- **多子级同步必须两轮匹配：先按下标，再按 Key 兜底。** 只按下标匹配的话，
  列表一重排状态就跟着槽位跑，输入框里的字会串到别的行——这正是"必须给稳定 Key"的原因。
- **在 layout / paint 阶段改状态会抛 `FrameError`。** 那是时序 bug 高发区，
  改了但本帧已经错过，表现为"界面下一帧才动"。框架选择响亮地失败。
- **命名用 snake_case**（`set_state` 不是 `setState`）。这是纯 Python 库，不是 Flutter 移植。
- **slot（flex 权重等）必须穿过组件层**。`Flexible(Button())` 里的 Button 是
  StatefulWidget，中间隔着 StatefulElement——slot 存在 Element 上随 update 透传，
  写死成 None 会让 flex 静默失效。子级同步 / 重排序同理：
  组件型子级的 RenderObject 要往下找（`render_object_of`），不能只看 `element.render_object`。
- **danger 变体不许发明 solid 红。** 语义令牌只定义了状态色的"浅底 + 深字"一对，
  硬造实心红等于发明新值，且在暗色主题下必翻车。danger 按钮 = 危险浅底 + 危险深字，
  明暗两版天然过 AA。
- **文字宽度不许估算。** Text 组件在 `text/` 的字体度量落地前不实现，
  更不能写"每字 14px"这种近似——中英混排和字号变化时会悄悄算错。
- **黄金图与生产帧的脏跟踪是矛盾的**。生产里"没脏就跳过整棵子树"省 99% 工作量，
  黄金图要的是"每帧完整画面"。devtools 用 `begin_frame(..., force_repaint=True)`
  强制走全树，标志会被记录但默认关掉——动画场景的优化不丢。
- **圆角描边必须画圆环，不能用"四条矩形条"拼。** 条的两端会被圆角裁掉，
  填充又是圆的，于是边框与圆角之间裂开露出底色（用户肉眼看到的就是
  "按钮四角有空白"）。正确做法：外圈圆角矩形 **减去** 内圈圆角矩形。
  `test_gfx_raster.py` 里有逐像素对照解析解的测试守着。
- **抗锯齿用 SDF（有符号距离场）做，不要用超采样。**
  每个像素算到边缘的距离，1px 内线性覆盖。它是纯浮点数学——不采样、
  不用随机数、不读时钟，IEEE 754 双精度 + 正确舍入的 sqrt 在各平台
  逐比特一致，所以**抗锯齿与确定性并不冲突**。
  （曾误以为"要确定性就不能有 AA"，那是把两件事错误地划了等号。
  超采样才真会拖慢 16 倍，SDF 是同数量级的开销。）

## 测试怎么写

- 布局引擎必须 **100% 无窗口可测**——不开窗口、不碰显卡，跑出全部几何。
- 关键行为要有测试钉住：无限约束报错、重复布局逐位一致、溢出可见、性能预算。
- 性能预算：1000 节点全量布局 < 5ms（docs/05 §8 的原值）。
  **断言的就是文档里那个数，不额外收紧**——墙上时钟断言在共享 CI runner
  上天生会抖，紧阈值会变成"偶尔红一次"，而一个偶尔红的门禁比没有更糟
  （大家会学会忽略 CI）。实测：本机 0.9ms，CI runner 约 2.1ms，余量足够。
  要更严的本地门禁：`INKSTONE_PERF_BUDGET_MS=2 pytest -m slow`。
- **性能断言只跑在 `perf` 任务里**，正确性任务用 `-m "not slow"` 排除它。
  否则正确性会莫名其妙地取决于 runner 当时有多忙。
- **架构约束也是测试**（`test_architecture.py`）：分层单向依赖、`text/` 无平台 API、
  组件零硬编码颜色。新增反向依赖会让 CI 红；登记表（`KNOWN_EXCEPTIONS`）
  里出现过期条目也会红——所以例外不会腐化成垃圾桶。
- **文本有专用的禁则测试集**（`test_text_stack.py`）：docs/04 §7 要求的
  行首禁则 20 例 + 行尾禁则 10 例，逐条参数化。改断行逻辑先看它红不红。

## 文档地图

设计文档 14 篇在 `docs/`，比代码更值得先读：

- `00` 愿景 · `01` 架构总览（含文档地图）· `02` 平台后端 · `03` 渲染管线
- `04` 文本与字体 · `05` **布局系统** · `06` 组件树状态事件 · `07` 样式与主题
- `08` 组件清单 · `09` 无障碍 · `10` 工程体系 · `11` 风险与取舍
- `12` 架构审查（诚实回答"能不能交给 AI 做成企业级"）· `13` **设计系统规格书**
- `ROADMAP.md` 分阶段 DoD

## 明确不做（1.x 范围外）

移动端、浏览器后端、游戏引擎级渲染、与 Qt/GTK 互操作、主题市场与付费组件。
写在这是为了避免"顺手做一下"把项目拖垮。

## 架构决策记录（ADR）

项目级的选型决定记在这里，避免同一个问题被反复讨论。

| 编号 | 决定 | 理由与影响 |
|---|---|---|
| ADR-0001 | 平台后端选 SDL2 | 完整 IME + 原生 Wayland，headless 用于测试（docs/01） |
| ADR-0005 | 整形与断行复用成熟实现，不自己造 | HarfBuzz 级整形规则上千条，自写=两年换更差版本（docs/04 §2） |
| ADR-0006 | **文本度量下沉到 L0 后端** | 度量是平台相关能力，放 L0 才不违反"平台差异不出 L0"；`Backend` 继承 `MetricsProvider` 让度量与绘制同源成为结构必然。`text/` 只面向协议说话，可 100% 无窗口测试 |
| ADR-0007 | **软件光栅默认用内置确定性字形** | 验证后端要的是"跨平台逐比特一致"与"布局可验证"，不是字形美观。ASCII 用内置 5×7 真位图，非拉丁用按 advance 定宽的占位块。**字形宽度必须画进 `advance` 里**——按字号自由决定宽度会让相邻字形重叠 |
| ADR-0009 | **真字形由 L0 平台引擎提供，度量与字形同一个对象** | `GdiFontEngine`（Windows）同时实现 `MetricsProvider` 与 `GlyphProvider`，共用一份 HFONT 缓存 → 字距与字形不可能分家。`devtools` 通过 `glyph_provider` 属性**自动配对**，杜绝"度量用一套、字形用另一套"（那会让字形叠字，且只在真机上可见）。度量归一化：逐簇 `GetCharABCWidthsFloatW` + 整串 `GetTextExtentPoint32W` 校正，避免整数累加漂移 |
| ADR-0010 | **Win32 文本 API 的长度按 UTF-16 码元算，不按码点** | `len(str)` 数的是码点，W 系 API 数的是码元。emoji 是代理对（1 码点 = 2 码元），传 `len()` 会让 GDI 只量/只画半个代理对——宽度变成一个荒唐的小数、字形变成空方框，**而且不报错**。统一走 `_utf16_len()` |
| ADR-0008 | **`text_run` 指令只吃字形不吃字符串** | docs/03 的约定落地：整形与断行在 L3 完成，渲染层只接收"哪些字形、画在哪"。换行规则、回退链、字素簇的知识不渗进渲染层。光栅层**按 `glyph.x` 画，不自己累加 advance**——否则字距调整/两端对齐/标点悬挂的决策会被静默丢掉 |

## 已知待办

- **真字形目前只有 Windows 一份**（`GdiFontEngine`）。macOS（CoreText）与
  Linux（FreeType）需要各自实现，接口（`MetricsProvider` + `GlyphProvider`）
  已经固定，照着 `gdi_fonts.py` 的结构写即可，上层零改动。
- **颜色 emoji 会退化成单色轮廓**：GDI 的灰度抗锯齿路径画不了 COLR/CBDT
  彩色字体，emoji 出来是黑白剪影。要真彩色需要 Direct2D/DirectWrite，
  属后续工作。宽度与位置是对的，不影响排版。
- **字号是整数像素**：GDI 的 `lfHeight` 只有整数，所以字号带小数时会被取整。
  想让 1.25 倍缩放等场景精确，得配合 DPI 那套（见 ROADMAP DoD）一起做。
- **`_fill` / `_stroke` 的裁剪边界有 1px 越界**：`_column_range` / `_row_range`
  用 `int(right) + 1` 当上界，裁剪边恰好落在整数像素上时会多放一列/一行。
  文本路径已用 `ceil` 修正；形状路径未改（会波及既有黄金图，需单独提交）。
- **架构上有 4 处已登记的反向依赖**（`test_architecture.py` 的
  `KNOWN_EXCEPTIONS`）：gfx/text → layout（几何原语，属共享内核）、
  backend → gfx、core → style。登记表不允许留失效条目，消除了就要删掉。
- `.gitattributes` 声明 `eol=lf`，但工作区多数 `.py` 实际是 CRLF。
  三平台 CI 上会产生幽灵 diff。修法：`git add --renormalize .`（会产生大 diff，单独提交）。
- **docs/13 §8 与实现有一处待裁决的分歧**：文档写"… → 状态 → 实例覆盖"，
  `style/resolve.py` 把**交互态放在最后**（否则实例 `bg=red` 会悄悄关掉 hover 反馈）。
  若按文档原文来，把 `resolve()` 里 state 与 overrides 位置对调即可。需要你拍板。
- Flex 还不支持 `wrap` 换行（docs/05 §4 有这条）。
- Scroll 只做了单/双向偏移。滚动条、锚点保持、过滚动按 ROADMAP 属于 Phase 2。
