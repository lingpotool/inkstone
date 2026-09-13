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
| `core/`（key / widget / element / render_object / binding / scope / signals） | ✅ 三棵树 + 帧调度 + 环境传播（`InheritedWidget` / `ThemeScope`）+ signals（`Signal` / `Computed` / `Effect`） |
| `backend/`（base / headless / sdl2（可选依赖 `inkstone[sdl2]` 提供 SDL2 二进制）/ **fonts** / **headless_fonts** / **fontfiles** / **hbft_fonts** / **fonts_data**） | ✅ 平台抽象层 + **字体度量契约**（`MetricsProvider`）+ **跨平台真字体引擎（HarfBuzz + FreeType）** + **内嵌兜底字体** |
| `gfx/color.py` | ✅ Color（hex 解析、插值、WCAG 对比度） |
| `style/`（tokens / theme / resolve / variants） | ✅ 三层令牌 + 明暗主题 + 变体解析 |
| `text/`（font / fallback / shaping / linebreak / paragraph / engine） | ✅ 字体度量、CJK 回退链、整形、断行（含禁则）、段落排版 |
| `widgets/`（basic / layout / form） | ✅ Box / Card / **Text** / Row / Column / Flexible / **ScrollView** / Button / **Input（可编辑：光标/选区/组合态/剪贴板）** |
| `gfx/`（display_list / paint / transform / **glyphs** / raster.base / raster.software / **raster.gl_driver + gl_backend**） | ✅ 显示列表（`TextRunOp` + `PathFillOp`/`PathStrokeOp`）+ 录制器（**仿射变换栈**）+ 软件光栅（**帧生命周期协议** + 不透明矩形快路径）+ PNG + **GL 后端骨架**（驱动接缝 + 假驱动测逻辑）与 **Windows WGL 真机驱动**（`backend/gl_wgl.py`，SDF 矩形/描边 + 字形纹理 + 离屏 FBO 回读；Linux GLX/EGL、macOS CGL 待做） |
| `devtools/screenshot.py` | ✅ 确定性截图 + 黄金图基线（12 张）+ **字形源自动配对** + DPI 档位 |
| `benchmarks/run.py` | ✅ 帧耗时基准（长列表滚动 / 全屏重绘 / 文本密集，p50/p95，GL 决策依据） |
| `events/ime.py` | ✅ IME 组合态模型（`ImeSession`：事件流 → text+composition 状态） |
| `events/focus.py` | ✅ 焦点系统：`FocusManager`（唯一焦点 / Tab 遍历 / 点空白失焦）+ `FocusNode`（`focus-visible` 语义：环只在键盘来源显示） |
| `events/pointer.py` | ✅ 指针路由：`HitTestResult` + 三阶段 `PointerRouter`（捕获/目标/冒泡、`stop_propagation`）+ ENTER/LEAVE 命中链差分；`layout.RenderBox.hit_test` 逆序命中 |
| `events/gestures.py` | ✅ 手势竞技场：`GestureArena` + `GestureRecognizer` 基类 + Tap / DoubleTap / LongPress / Drag；按 pointer_id 竞争裁决、取消是一等公民、超时用注入时间轴推进 |
| `examples/hello.py` | ✅ 可运行示例（`--dark` / `--deterministic`），进 CI 冒烟测试 |
| `examples/notes.py` | ✅ **样板 App「墨记」**：侧栏 + 滚动列表 + 表单 + 明暗主题切换；headless 出黄金图、`--sdl2` 真窗口交互，进 CI 冒烟 |
| 其余模块（gfx GL+Skia / events 其余 / primitives / app …） | ⬜ 占位桩 |

1089 个无头单测全绿，**黄金图像素级比对**也跑通（12 张基线）。
**地基整改 R1（正确性止血，docs/15）、R2（测试求真，docs/16）、
R3（渲染协议重塑，docs/17）、R4（跨平台文本栈，docs/18）、
R5（事件与 IME，docs/19）、R6（主题传播与依赖追踪，docs/20）已完成**：
R1 修掉 11 处静默断链与崩溃级 bug；
R2 让门禁本身说真话（黄金图改像素比对、基线缺失即失败、真全量性能基准、
数值硬编码扫描）；R3 趁消费者少把渲染协议改对（帧生命周期、
`PositionedGlyph.y_offset`、仿射变换栈、path 指令形状、光栅快路径）；
R4 把文本栈换成 Flutter/Chrome 同路线（HarfBuzz 整形 + FreeType 光栅化 +
内嵌 Inkstone Sans 兜底字体），删掉 GDI 路线，黄金图全部切真字体；
R5 把输入命脉修通：事件模型加 window_id、时间戳用事件自带值、
`wait_events` 不再丢唤醒事件、滚轮方向/精度/坐标、scancode 与 keysym 分离、
**IME 通道从物理不通变成可用**（start_text_input / set_ime_rect 进协议、
TEXTINPUT 与组合态拆成两条通道）、呈现契约进协议、GLFW 空壳删除；
R6 落地环境传播与状态双轨：`InheritedWidget` / `ThemeScope` 子树覆盖 +
定向标脏（改侧栏主题，主区零重建）、`Signal` / `Computed` / `Effect`
（与 Inherited 共用"读时登记、写时标脏"内核，1000 次写 = 1 次帧）、
变体状态配方逐变体补齐（全变体×全状态过 WCAG AA 断言，
顺手修掉暗色 `primary_press` 3.6:1 的违规令牌）。
每条都带"修复前必红"的回归测试。地基整改 R1–R6 全部完成，
后补清单见 docs/20 §R6.4。
收官包 **R7「交互闭环与 Phase 1 收尾」（docs/21）正在进行**：
R7.1 命中测试与指针事件路由已完成（`RenderBox.hit_test` 逆序命中 +
滚动视口裁剪、三阶段路由与 `stop_propagation`、ENTER/LEAVE 差分、
Input 聚焦经 owner 钩子打开 IME 通道并上报候选框位置）；
R7.2 手势竞技场已完成（`GestureArena` + Tap/DoubleTap/LongPress/Drag，
按 pointer_id 竞争裁决、输家收到 cancel 退回 ACTIVE、8px/500ms/300ms 进令牌、
超时经 `begin_frame(now_ms=...)` 用注入时间推进）；
R7.3 DPI 缩放接线已完成（档位经 `begin_frame(dpi_scale=…)` 进上下文，
`flush_paint` 在根上压缩放 → 显示列表是设备像素，布局仍是逻辑像素；
DPI_CHANGED 下一帧生效不拉伸；150% 黄金图）；
R7.4 样板 App「墨记」（`examples/notes.py`）已完成：侧栏 + 滚动列表 +
表单 + 明暗主题切换，`ScrollView` widget 补齐，headless 黄金图进 CI、
`--sdl2` 真窗口交互；它逼出并修掉一个文本栈真 bug（多字重下光栅选错字体面
→ 粗体整行画成别的字，见 ADR-0016）；
R7.5 性能基准已完成（`benchmarks/run.py` 三场景出 p50/p95，规则先写死），
数据触发 GL 后端子包（ADR-0017 / docs/22）。测试 888 → 944 全绿；
GL 子包 R8.1（驱动接缝 + 后端逻辑层）、R8.2（Windows WGL 真机驱动）、
R8.3（热路径缓存 + 矩形合批 + 字形图集 + 帧去重）、
R8.4（显示列表状态指令 + 滚动 repaint boundary + GL 层缓存）已完成，
测试 → 1089 全绿（覆盖率 90.15%）。**R7.5 验收达成：GL p95 全屏 4.4ms /
文本 8.6ms / 滚动 1.0ms，三场景全部 ≤10ms**（软件为事实源，未变）。
R8.5 SDL2 走声明式可选依赖、R8.6 真窗口 GL 上屏完成（SDL 上下文 + FBO blit +
换链，Windows 真机验证）。R9 焦点系统 + 文本编辑落地（ADR 无新增，见
`events/focus.py` 与 `widgets/form.py`）；R10 窗口身份与能力落地（ADR-0022）：
原生边框 + 平台化外观（AppUserModelID / DWM 标题栏配色 / 程序化图标 / 最小尺寸），
应用外壳 `inkstone.app` 与 `examples/notes.py --sdl2` 已接线。
剩余：Linux/macOS GL 驱动、路径三角化、通用脏矩形、应用外壳脏区调度、
窗口位置尺寸记忆、macOS NSWindow appearance。

**R7 整包 + Phase 1 的"交互闭环"已闭合**（能点、能滚、能打字、能切主题、
能缩放、真窗口观感专业）。Phase 1 剩余 DoD：macOS/Linux 真机字体验证、
三平台 SDL2 交互验证、文本场景 p95 进 8ms。
**GL 后端子包（docs/22）进行中**：R8.1 驱动接缝 + 逻辑层、R8.2 Windows WGL
真机驱动、R8.3 热路径 + 合批 + 图集 + 帧去重、R8.4 状态指令 + 层缓存已完成，
三场景 p95 ≤ 10ms。R8.5 把 SDL2 二进制改成**声明式可选依赖**
（`inkstone[sdl2]`，ADR-0020）并在 Windows 真窗口验证了窗口/DPI/事件/IME/
剪贴板（顺带修掉指针返回值未声明 `restype` 的访问违例）。剩余：真窗口 GL
present、Linux/macOS GL 驱动、路径三角化、通用脏矩形。
渲染与文本这几块已经能出**看起来像正经软件**的界面。

**字体有三种来源，各司其职（ADR-0007 / ADR-0011）**：

- **真字体引擎**（`HbFtFontEngine`，生产与示例默认）：HarfBuzz 整形 +
  FreeType 光栅化，三平台同一套；系统字体缺失时由**内嵌 Inkstone Sans**
  兜底（中文永不出豆腐块）→ App、示例、真机预览用这条。
- **黄金图真字体**（`tests/real_font.py` 的 `golden_owner`）：同一套 HB+FT，
  但 `FontLibrary(directories=())` 只装内嵌字体 → 黄金图与 CI 用这条，
  跨平台逐比特一致。
- **确定性字形表**（`HeadlessMetrics`，测试默认）：内置 5×7 位图 +
  非拉丁占位块 → 文本层逻辑断言（advance、断行位置）用这条，
  断言数值与任何真字体无关。

三者都是"度量与字形同源"，区别只在源头。`devtools` 会**自动配对**：
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
| `coverage` | `-m "not slow"` + `--cov-fail-under=85`，**在 Windows 上跑** | 平台独占代码只能在自己的平台上量 |
| `perf` | `-m slow`，**无插桩** | 覆盖率插桩会让性能断言随机变红，两者必须分开 |
| `architecture` | `test_architecture.py` | 分层被破坏要在 CI 上有指名道姓的红点 |

**踩过的坑（值得记住）**：
1. 最初 `test` 任务跑全量测试（含 `slow`），结果 py3.10 的共享 runner 上
   性能断言以 2.10ms 对 2.0ms 挂了。墙上时钟断言天生会抖——正确性任务必须排除它。
2. 覆盖率任务原本在 Ubuntu 上跑，但当时 Windows 专有的真字体引擎在 Linux 上
   只能整段跳过，于是 800 行全被算成"未覆盖"（89% → 83%）。**与其 omit 掉
   （等于对自己最核心的代码闭眼），不如在能跑它的平台上量。**
   该引擎已在 R4.6 被跨平台的 HB+FT 取代（GDI 路线删除），但原则保留：
   将来再出现平台独占代码时，覆盖率仍要在能跑它的平台上量。

黄金图的 CI 行为值得说清：**三平台像素级一致，已经在 CI 上验证过了**
（`77fed3a` 首次推送即 8 个任务全绿）。无头度量表是纯数据、
软件光栅是纯算术、PNG 编码固定参数，全程不碰系统字体/时钟/随机数。
若哪天出现平台间差异，那是确定性的某条链路破了，正是要立刻知道的事。
（真字形后端上线后会引入平台差异，届时按 `tests/golden/<platform>/` 分目录。）

**比对单位是解码后的 RGBA 像素，不是 PNG 文件字节**（R2.1 纠正）：
`encode_png` 用 `zlib.compress(raw, level=6)`，而 deflate 的输出**不跨 zlib
版本保证一致**，各平台 CPython 打包的 zlib 并不固定。拿文件字节比对，
"三平台一致"是侥幸；哪天某个平台换了 zlib，黄金图会以"渲染变了"的名义红一次，
把所有人引向错误的排查方向。解码器在 `tests/png_compare.py`（纯 stdlib，
不引 Pillow），5 种行滤波器都实现——只认 8-bit RGBA 非交错，
其余形态**响亮报错**，不"尽力而为"地解。
同一条纪律的另一半：**基线缺失 = 失败**。此前"缺失就顺手写一份然后绿灯"
等于把门禁做成了自动橡皮图章，新增场景与"基线被误删"完全无法区分。

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
- **ctypes 绑定必须显式声明签名，尤其是指针返回值。** 默认 `restype` 是
  `c_int`，64 位平台上会把 `SDL_Window*` 之类的指针**截断成 32 位**——
  句柄作废，下一次传给别的函数就是访问违例（R8.5 真机验证时撞到，
  之前没有真机窗口测试所以一直潜伏）。`SDL_*` 的签名集中在
  `backend/sdl2.py::_bind_signatures`，新加函数时去那里登记；
  返回 `float` 的函数（如 DPI scale）同样要声明，否则按 int 解读。
- **手势识别器由组件层创建、经元素写进渲染对象（R7.2）。** 识别器要令牌阈值
  （L6），而 `RenderObject` 拿不到主题——所以 Button/Input 在 `State.build` 里用
  `context.theme.gesture(...)` 造识别器，元素写进 `render_object.recognizers`，
  Router 命中时收集并交给竞技场。组件**不许**直接解释裸 DOWN/UP 判点击——
  那是竞技场的裁决权（ADR-0014）。
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
- **布局真的跑过就必须标绘制脏。** `RenderBox.layout()` 在缓存未命中时
  调 `mark_subtree_needs_paint()`，把**自身与所有后代**都标脏。
  判据刻意不是"尺寸变了没有"——滚动、resize 这类主场景里子级尺寸一个像素
  都没变，变的是父级给它定的**位置**；而子级自己的布局因为约束没变会走缓存，
  于是它永远标不上脏（表现就是"滚了但画面不动"，且不报错）。
  子树标脏不能写成"节点已脏就提前收工"：`mark_needs_paint` 是向上冒泡的，
  叶子标脏会把祖先带上，祖先的其他子级却还是干净的。
- **滚动容器必须裁剪视口。** `RenderBox.paint_clip() -> Rect | None` 是钩子
  （默认 None），`paint_tree` 进入子树前施加、出来后恢复；`RenderScroll`
  覆写它返回自己的 bounds。没有它，滚出视口的内容会直接糊在下面的组件上。
- **`update_render_object` 之后必须 `mark_needs_layout()`。** 只标 paint 的话，
  改宽高 / padding / flex 这类几何属性会静默失效（不报错、不崩溃、界面不动）。
  `layout()` 自带约束缓存兜底，没有几何变化时开销极小。
- **复用子级时槽位（flex 权重）必须跟着更新，且槽位变化要触发重挂。**
  只看"顺序变没变"会漏掉权重变化——顺序不变时渲染树上的旧权重会一直留档，
  表现为"改了 flex 权重，布局纹丝不动"。
- **build 抛异常不许打断整批。** `flush_build` 逐节点 try/except：错误记进
  `owner.errors`（带节点路径），失败节点保持脏等下一帧，同帧最多重试
  `MAX_BUILD_FAILURES_PER_FRAME` 次；全部处理完后若非空则抛聚合的 `BuildError`。
  "响亮失败"不等于"让半棵树停在旧状态"。
- **"需要一帧"要有出口。** `BuildOwner.on_frame_scheduled`（无参回调）在脏集合
  从空变非空时触发一次，`begin_frame` 末尾注销标志。纯绘制脏由知道 owner 的
  那一层调 `request_frame()`——`RenderObject` 拿不到 `BuildOwner`。
- **换主题 = 整棵树标脏重建。** `BuildOwner.theme` 是 property，setter 里
  全树 `mark_needs_build`。样式只在 mount / update 时写进渲染对象，
  不标脏的话换主题会静默保留旧色（正式机制见 docs/20）。
- **光栅后端是一帧的四个阶段，不是一次函数调用。**
  `begin_frame(size, scale) → execute(display_list, clip)* → end_frame() → screenshot()`。
  帧缓冲**归后端所有**：`screenshot()` 是显式读回，不是后端的唯一出口——
  GL/Skia 的正常路径是"画进 GPU 表面再交换"，把像素读回内存是异常操作。
  早期的 `rasterize() -> FrameBuffer` 让这两个后端在结构上无法实现，
  已删除（R3.1）。顺序用错抛 `RasterError`，不猜。
- **脏矩形是 `execute` 的参数，不进显示列表。** 显示列表保持"纯图纸"：
  同样的组件树永远产出同样的指令序列，黄金图的比对语义不被重绘策略污染。
- **`PositionedGlyph` 的四个偏移与 HarfBuzz 一一对应**
  （`x / y_offset / advance / y_advance`）。`y_offset` 向上为正，缺了它
  上下标、组合符、CJK 标点悬挂、多字体回退的基线差全都表达不出来（R4 会撞墙）。
- **变换是 2×3 仿射矩阵，不是"累计平移"。** `Affine` 不可变、可比较、
  可序列化；`A.then(B)` = 先 A 后 B。录制器用
  `op.then(current)`（新操作套在已有变换**外面**），所以"先定位再缩放"
  = 绕自己的原点放大，元素不会一边变大一边跑掉。
  等比缩放下线宽、圆角、字形度量一起缩放；非等比下**不缩放**（圆角会变成
  椭圆角，当前指令形状表达不了）——明确不支持好过半个实现悄悄画错。
- **路径用扁平 verb 数组，不接受 SVG path 字符串。** `(verb, *coords)`，
  verb ∈ {M,L,Q,C,Z}；形状在构造时校验（非法路径当场抛，不在光栅时画一半）。
  字符串会让"同样的路径"有两种写法，显示列表的逐指令等值比对立刻失效。
  软件光栅暂未实现 → **抛 `NotImplementedError`**，不静默跳过。
- **软件光栅的快路径必须与通用路径逐比特相同。** 判据是
  "不透明 + 直角 + **精确**像素对齐"（不用容差：容差会让边界上的两张图
  差一个像素的覆盖度）。`SoftwareRasterizer(fast_paths=False)` 可以关掉它，
  测试就是靠这个证明等价的。全屏填充因此从 749ms 降到 3ms。
- **亚像素字形落位默认关。** 默认字形源是**位图**掩码，本来就按像素栅格生成，
  用小数相位重采样只会把笔画摊薄（实测 text_block 5.6% 像素变化、多数"变亮"）。
  R4 换上 FreeType 的**轮廓**掩码后打开才是收益（`subpixel_glyphs=True`）。

## 测试怎么写

- 布局引擎必须 **100% 无窗口可测**——不开窗口、不碰显卡，跑出全部几何。
- 关键行为要有测试钉住：无限约束报错、重复布局逐位一致、溢出可见、性能预算。
- 性能预算分两条，**名字要说清测的是什么**（R2.3 的教训）：
  1000 节点**增量**布局（只标脏根节点）< 5ms；1000 节点**全量**布局
  （`mark_subtree_needs_layout()`，整树标脏）< 30ms。
  此前只有一条"全量 0.89ms"，但它每轮只调 `root.mark_needs_layout()`——
  该方法只向上冒泡，于是第二轮起子级全部命中缓存：那个数字测的是
  "一次根布局 + 250 次缓存查询"，真全量是它的 16 倍。**假测量比没有测量更危险**，
  它会让真正的数量级回归从门禁底下溜过去。
  **断言的就是文档里那个数，不额外收紧**——墙上时钟断言在共享 CI runner
  上天生会抖，紧阈值会变成"偶尔红一次"，而一个偶尔红的门禁比没有更糟
  （大家会学会忽略 CI）。实测：增量 1.7ms、全量 17.6ms（本机）。
  要更严的本地门禁：`INKSTONE_PERF_BUDGET_MS=2 pytest -m slow`
  （另有 `INKSTONE_PERF_FULL_BUDGET_MS` 管全量那条）。
- **性能断言只跑在 `perf` 任务里**，正确性任务用 `-m "not slow"` 排除它。
  否则正确性会莫名其妙地取决于 runner 当时有多忙。
- **架构约束也是测试**（`test_architecture.py`）：分层单向依赖、`text/` 无平台 API、
  组件零硬编码颜色。新增反向依赖会让 CI 红；登记表（`KNOWN_EXCEPTIONS`）
  里出现过期条目也会红——所以例外不会腐化成垃圾桶。
- **文本有专用的禁则测试集**（`test_text_stack.py`）：docs/04 §7 要求的
  行首禁则 20 例 + 行尾禁则 10 例，逐条参数化。改断行逻辑先看它红不红。

## 文档地图

设计文档在 `docs/`，比代码更值得先读：

- `00` 愿景 · `01` 架构总览（含文档地图）· `02` 平台后端 · `03` 渲染管线
- `04` 文本与字体 · `05` **布局系统** · `06` 组件树状态事件 · `07` 样式与主题
- `08` 组件清单 · `09` 无障碍 · `10` 工程体系 · `11` 风险与取舍
- `12` 架构审查（诚实回答"能不能交给 AI 做成企业级"）· `13` **设计系统规格书**
- **`14`–`22` 地基整改与收尾系列（施工中优先读）**：`14` 施工总纲（含执行协议与已拍板决策）、
  `15` R1 正确性止血 · `16` R2 测试求真 · `17` R3 渲染协议重塑 ·
  `18` R4 文本栈替换（HarfBuzz+FreeType）· `19` R5 事件与 IME · `20` R6 主题传播与依赖追踪 ·
  `21` R7 交互闭环与 Phase 1 收尾 · `22` GL 后端子包（R7.5 数据驱动的决策与范围）
- `ROADMAP.md` 分阶段 DoD

**当前优先级**：地基整改（docs/14–20）与收官包 R7（docs/21）均已完成。
按 R7.5 的数据驱动决策，当前主线是 **GL 后端子包（docs/22）**；
Phase 1 其余 DoD（文本编辑模型、macOS/Linux 真机字体验证、三平台 SDL2 验证）
并行推进。任何新功能开发前，先确认对应分包文档里该领域没有未完成的条目。

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
| ADR-0009 | ~~真字形由 L0 平台引擎提供~~（被 ADR-0011 取代） | "度量与字形同一个对象"的精神被 HB+FT 继承；GDI 实现已随 R4.6 删除 |
| ADR-0010 | **Win32 文本 API 的长度按 UTF-16 码元算，不按码点** | 历史教训（GDI 已删）。emoji 是代理对（1 码点 = 2 码元），传 `len()` 会让 W 系 API 只量/只画半个代理对，**而且不报错**。将来再碰 Win32 文本 API 时这条仍然成立 |
| ADR-0011 | **文本栈自带 HarfBuzz + FreeType，内嵌兜底字体，删除 GDI 路线** | 三套平台原生引擎在数学上不可能达成"三平台行宽一致"（Flutter/Chrome/Android 的答案一致：自带 HB+FT）。uharfbuzz / freetype-py / fonttools 为运行时依赖（合计约 22MB，有预编译轮子）；包内嵌 Inkstone Sans（Noto Sans SC 子集，OFL，约 1.8MB）做回退链终点——**中文永不出豆腐块**是结构保证。字号走 26.6 定点，17.5px 不取整。黄金图用只装内嵌字体的引擎（`tests/real_font.py`），跨平台逐比特一致 |
| ADR-0008 | **`text_run` 指令只吃字形不吃字符串** | docs/03 的约定落地：整形与断行在 L3 完成，渲染层只接收"哪些字形、画在哪"。换行规则、回退链、字素簇的知识不渗进渲染层。光栅层**按 `glyph.x` 画，不自己累加 advance**——否则字距调整/两端对齐/标点悬挂的决策会被静默丢掉 |
| ADR-0012 | **MetricsProvider 拆出 Backend 协议；协议符合性用显式遍历测试** | SDL2Backend 自称实现 Backend 却缺全部度量方法——`runtime_checkable` 不查方法体，假保证比没保证更危险。拆分后：窗口后端管窗口/输入/帧边界，度量引擎（HB+FT）由 App 组装时注入。符合性由 `tests/unit/test_backend_protocol.py` 逐成员断言，且测试自身能红。同批：事件模型加 `window_id`、IME 拆 TextEvent/ImeEvent 双通道、呈现帧边界（begin/end_frame）进协议 |
| ADR-0013 | **命中链的"顺序与分发"归 events（L1），"坐标与几何"归 layout（L4）** | L1 不能 import L4，所以 `HitTestResult` / `PointerTarget` / 三阶段 `PointerRouter` 定义在 `events/pointer.py` 且不含任何几何类型；`layout.RenderBox.hit_test` 反向（L4→L1 合法）填充命中链、逐层扣掉子级 offset，局部坐标因此随链传递。命中链 **target 优先**（逆序递归子级 = 后画的在上层），滚动裁剪由"视口 bounds 检查先于递归"结构成立，不写特判。**ENTER/LEAVE 不信后端**（SDL 的是窗口级），由 MOVE/DOWN/UP/WHEEL 的命中链差分生成——把 DOWN 也算进来是为触屏（无悬停）。事件派发在 layout/paint 阶段一律抛 `FrameError` |
| ADR-0014 | **tap/double-tap/long-press/drag 是竞技场里的竞争，不是各自判断；单击与双击必须在同一个识别器里裁决** | 滚动列表里放按钮时"按钮 ACTIVE + 列表一起滚"是两个都赢的经典 bug——`GestureArena` 按 pointer_id 收集命中链上的识别器并显式裁决，输家收 `on_reject`（取消），组件据此退回 ACTIVE。单击与双击**不能**拆成两个识别器：第一击抬起时 Tap 无法知道第二击来不来，Tap 先赢则双击永不出现，Tap 等待则无法在第二击时撤回已发的单击；`DoubleTapGestureRecognizer` 用"延迟的单击 + 双击"一个状态机闭合。识别器只吃构造时传入的令牌阈值（8px / 500ms / 300ms），时间一律用事件 `time_ms` + `begin_frame(now_ms=)` 推进，不读墙上时钟 |
| ADR-0015 | **DPI 换算只在录制/光栅层：布局与事件恒为逻辑像素，物理 = 逻辑 × dpi_scale** | 组件里乘缩放因子会让几何、命中、事件坐标三处口径分叉（改一处漏两处）。档位经 `BuildOwner.begin_frame(dpi_scale=…)` 进帧上下文，`flush_paint` 在根上压一个等比仿射变换，于是显示列表指令是设备像素、帧缓冲按逻辑尺寸×scale 分配，而组件代码一行不改。线宽/圆角/字形 em 随仿射一起缩放（R3.4），文字在物理分辨率上重新光栅化——掩码缓存键含生效字号，1.0 档的掩码不会被复用到 1.5 档。`DPI_CHANGED` 经 `handle_window_event` 更新档位，下一帧按新档重录，不拉伸旧帧 |
| ADR-0016 | **字形 id 必须与"产生它的字体面"（face_key）同源，光栅不许按 family 重选面** | glyph id 只在它所属的 face 里有意义。同一 family 的 Regular 与 Bold 是两个文件、两套编号；整形按 `spec.weight` 选面，而 `mask_for` 曾按 family 用 REGULAR 重选——于是"用 Bold 的 id 查 Regular 的轮廓"，粗体中文整行画成别的字（R7.4 样板 App 的标题栏暴露）。修法：`GlyphPlacement.face_key`（path+index）经 `ShapedCluster` / `PositionedGlyph` 一路带到 `mask_for(face_key=…)`；掩码缓存键也改用 face_key（否则两个字重互相顶掉）。合成字体回归测试钉住（`TestGlyphsComeFromTheShapedFace`）。这条是"度量与字形同源"从"同一个对象"加强到"同一个面" |
| ADR-0017 | **GL 后端由 R7.5 数据驱动启动：软件光栅只做确定性事实源，不做生产帧率** | 决策规则在施工前写死（`benchmarks/run.py`，p95 > 10ms = 60fps 预算六成）。实测三场景 p95 为预算的 40–300 倍（scroll 859ms / fullscreen 3125ms / text 576ms），热点是纯 Python 逐像素 SDF 与"整份显示列表全量光栅"。故启动 GL 后端子包（docs/22）：只实现现有 IR 指令集、沿用 R3.1 帧生命周期、不改 core、不引第二套文本栈、黄金图仍以软件光栅为准。**量完再调阈值等于给结论找理由**，数字与规则一并留在 docs/22 |
| ADR-0018 | **GL 后端先切"驱动接缝"并把逻辑层测透，真机 GL 调用后置；两个光栅后端能力必须对等** | GL 里只有建上下文/传纹理/draw call/读像素属于 GPU，其余（帧状态机、op.clip×脏矩形取交→scissor、半径钳制、文本取掩码与连字规则、资源生命周期、读回校验）都能无显卡测试。`GLDriver` 协议 + `FakeDriver` 记录调用序列，把 R8.1 做成了**可验证**的一步；真机 ctypes 驱动（R8.2）只需照协议填，后端逻辑不改。同时规定：**一个后端会画的指令，另一个也必须会**（路径两后端都抛 `NotImplementedError`），否则差异会拖到黄金图比对时才暴露。取字形规则 `glyph_mask_plan` 抽成两后端共用的唯一副本 |
| ADR-0019 | **显示列表增加状态指令（变换/裁剪）与层标记；滚动/静态子树作为 repaint boundary 做 RasterCache** | 坐标在录制时被 bake 成绝对值，导致滚动每帧都要把新偏移重新烤进几百条指令、并重排重绘整棵子树——这是滚动做不到专业帧率的根因（Flutter 的答案是 layer + repaint boundary，Skia 是 damage）。落地：`PushTranslateOp`/`PushClipOp`/`PopOp` 作为运行时状态，`resolve_state_ops` 保证与烘焙**逐像素等价**（两个后端共用，黄金图不动）；`PushLayerOp(key, rect)` 标记可缓存层，GL 后端渲染进离屏 FBO 纹理、命中 key 即复用，每帧只画一个四边形。配套两条纪律：**光栅后端不自动清屏**（背景由显示列表的指令负责，否则脏子树重绘会擦掉未变区域）；**层 key 用单调代号而非 `id()`**（对象回收后 id 复用会让旧纹理顶包） |
| ADR-0020 | **SDL2 二进制走"声明式可选依赖"，不在仓库放二进制；`load_sdl2` 三层优先级** | 平台窗口后端（ADR-0001）需要各平台 SDL2 二进制。专业做法不是往仓库/源码树塞 DLL，而是可选 extra `inkstone[sdl2] = pysdl2 + pysdl2-dll`：`pysdl2-dll` 发布 Windows/macOS/Linux 预编译 wheel（≈4MB，含 SDL2.dll ≈1.5MB），`pysdl2` 按平台定位；我们的 backend 仍 ctypes 直调，只借它"找到库"。加载优先级：`INKSTONE_SDL2` 环境变量（打包/私有部署）> 可选依赖 > 系统库（winget/brew/apt），全失败抛带三条修复指引的 `BackendError`。**核心包保持零依赖**——SDL2 只在开真窗口时需要，测试/CI/黄金图/基准全走无头后端 |
| ADR-0021 | **真窗口 GL 上屏：上下文归 SDL，GL 只借；离屏 FBO 是唯一绘制目标，`end()` 时 blit 上屏** | 自建窗口（WGL 隐藏窗口）解决不了"显示到窗口"：GL 资源与上下文绑定，纹理跨上下文不可用。做法是 `WindowSpec(opengl=True)` 让 SDL 用 OPENGL 标志建窗，`sdl_gl_driver(backend, window)` 在其上下文上装配同一套驱动（`SDL_GL_CreateContext` + `SDL_GL_GetProcAddress` + `SwapWindow`，`close()` 只解除不销毁）。绘制**始终进离屏 FBO**（层缓存/读回/尺寸口径都建立在它上面），`end()` 把 FBO 纹理 blit 到默认帧缓冲再换链——"窗口"与"离屏"画出来的是同一张图。配套坑：挂载模式下 `begin()` **不得**无条件 `wglMakeCurrent(0,0)`，那会把 SDL 的上下文解绑，之后所有 GL 调用静默失败（实测 FBO 完整性校验返回 0） |
| ADR-0022 | **保留原生窗口边框，只做平台化外观（图标 / AppUserModelID / DWM 标题栏配色），不自绘标题栏** | Win11 的 Snap Layouts、贴靠、最大化动画、无障碍与高对比主题都是 DWM 提供的能力；自绘标题栏（Chrome / VS Code 路线）要自己实现 hit-test 与 snap，且必然丢掉系统能力。Flutter / Electron 的默认路线是原生边框 + 平台化外观，我们照做：`Backend` 协议加 `set_app_identity`（任务栏身份，须在建窗前设）/ `set_title` / `set_min_size` / `set_maximized` / `set_fullscreen` / `set_window_theme` / `set_icon`；Windows 细节收在 `backend/windows_shell.py`（`SetCurrentProcessExplicitAppUserModelID` + `DwmSetWindowAttribute`，非 Windows 一律安全空操作返回 False）。**图标不进仓库**：`inkstone.app.app_icon_rgba` 用自家显示列表 + 软件光栅画图标（圆角方石 + 环形砚池 + 墨点），同一份代码逐字节确定，`tools/build_icon.py` 只在打包时导出 .ico。配套坑：`SDL_SysWMinfo` 是"版本 + 子系统 + union"，SDL 会**整段写入**，按"只声明 HWND"定义结构会栈越界（实测 DWM 调用处 access violation）——必须照实声明并留余量；窗口 resize 后视口尺寸跟 `WINDOWEVENT_RESIZED` 走 |

## 已知待办

- **颜色 emoji 目前是灰度轮廓**：FreeType 的 `FT_LOAD_RENDER` 走灰度抗锯齿，
  画不了 COLR/CBDT 彩色位图。要真彩色需接入彩色字形格式，属后续工作。
  宽度与位置是对的，不影响排版。
- **内嵌兜底字体是 GB2312 6763 字 + 拉丁/希腊/西里尔/假名**：生僻字与韩文
  不在覆盖内（口径与加档方法见 `tools/build_embedded_font.py` 头注释）。
- **架构上有 4 处已登记的反向依赖**（`test_architecture.py` 的
  `KNOWN_EXCEPTIONS`）：gfx/text → layout（几何原语，属共享内核）、
  backend → gfx、core → style。登记表不允许留失效条目，消除了就要删掉。
- `.gitattributes` 声明 `eol=lf`，但工作区多数 `.py` 实际是 CRLF。
  三平台 CI 上会产生幽灵 diff。修法：`git add --renormalize .`（会产生大 diff，单独提交）。
- Flex 还不支持 `wrap` 换行（docs/05 §4 有这条）。
- Scroll 只做了单/双向偏移。滚动条、锚点保持、过滚动按 ROADMAP 属于 Phase 2。
