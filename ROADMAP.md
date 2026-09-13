# 路线图

> 这不是愿望清单，是**带验收标准的交付计划**。
> 每个阶段都有明确的"做完的定义（DoD）"，达不到就不许进入下一阶段。
> 理由很简单：UI 库最怕的就是"看起来完成了 80%，但那 20% 是地基"。

## 全局策略：垂直切片优先

**不先做通用组件库，先做一个真实的软件。**

通用库最容易死在"每个控件都能用一点，但拼不出一个能用的软件"。
所以 Phase 1–2 会让一个真实应用（样板 App）驱动优先级：
控件只有在真实场景里被用顺了，才算完成。

---

## Phase 0 · 探针 —— 已完成 ✅

**目标**：证明最不确定的三件事站得住。

| 风险 | 结论 |
|---|---|
| Python 自绘能否做到不丑 | ✅ 能（卡片/圆角/明暗主题已跑通） |
| 中文渲染与测量是否正常 | ✅ 正常（微软雅黑，文本测量精确） |
| 简化版 flex 是否够用 | ✅ 够用于简单界面，但需换成正规约束模型 |
| 能否无头自检 | ✅ 能（截图验收闭环成立） |

**产物**：`prototype/`（约 1100 行，已冻结，不参与打包）

---

## Phase 1 · 地基（Foundation）

**目标一句话**：能用它写出一个真实的小工具，且在三台机器上长得一样。

### 交付

1. **平台抽象层**：`backend/base.py` 协议 + SDL2 实现 + headless 实现
   - **进度（已实现）**：`backend/base.py`（归一化事件 + Backend 协议）、
     `backend/headless.py`（无头后端，完整测试）、`backend/sdl2.py`（ctypes 绑定）。
     40 个测试：窗口生命周期、注入式事件、**注入式时钟**、DPI 变化广播、
     键名归一化（纯函数，无 SDL2 也能测）。
   - 覆盖范围说清楚：SDL2 的 `create_window` / `pump_events` 需要真窗口与输入设备，
     CI 跑不了，代码里已标注"未在 CI 覆盖"；能在无环境测的部分都拆成纯函数测了。
2. **渲染管线**：显示列表 + 自研 GL 光栅后端（先不引入 Skia，减少变量；Skia 属 Phase 3）
   - 矩形 / 圆角矩形 / 描边 / **文本** / 裁剪
   - **进度（部分完成）**：显示列表（含 `TextRunOp` / path 指令）、仿射录制器、
     软件光栅（矩形 / 圆角 / 描边 / 抗锯齿 / **文本** / 裁剪 / 帧生命周期）、
     PNG 编码、DPI 缩放接线（R7.3）已实现。
     字形有两套来源，都是"度量与字形同源"（ADR-0007 / ADR-0011）：
     **确定性字形**（跨平台逐比特一致，黄金图用）与
     **跨平台真字体**（`HbFtFontEngine`：HarfBuzz 整形 + FreeType 光栅化 +
     内嵌 Inkstone Sans 兜底，中文渲染成真汉字，三平台同一套；GDI 路线已删）。
     `devtools` 自动配对字形源与度量源，配错会立刻可见。
     **自研 GL 光栅后端**（R7.5 基准触发，立项 `docs/22`）：R8.1 驱动接缝 +
     逻辑层、R8.2 Windows WGL 真机驱动、R8.3 热路径缓存 + 合批 + 字形图集 +
     帧去重、R8.4 状态指令 + 滚动 repaint boundary + GL 层缓存已完成。
     **R7.5 验收达成**：本机 p95 全屏 4.4ms / 文本 8.6ms / 滚动 1.0ms，
     三场景 ≤10ms。尚缺：真窗口 GL present（SDL2 GL 窗口 + swap）、
     Linux GLX/EGL 与 macOS CGL 驱动、路径三角化、通用脏矩形损伤跟踪、
     阴影/渐变/彩色 emoji。**注意**：软件光栅是黄金图的确定性事实源，
     不是帧率后端（见 docs/22）。
3. **文本**：字体加载与度量 + 单行/多行排版 + **CJK 字体回退链**
   - **进度（已完成）**：`backend/fonts.py`（度量契约，全库唯一入口）、
     `backend/headless_fonts.py`（确定性度量表 + 字素簇）、`text/font.py`
     （样式/解析/注册）、`text/fallback.py`（三平台 CJK/emoji 回退链）、
     `text/shaping.py`（按脚本分段整形）、`text/linebreak.py`
     （UAX #14 核心 + 完整 CJK 禁则）、`text/paragraph.py`
     （对齐/行高/省略号/命中测试/选区）、`text/engine.py`（门面）。
     123 个测试，含 docs/04 §7 要求的 20 例行首禁则 + 10 例行尾禁则。
   - 关键决定：**度量下沉到 L0 后端**（ADR-0006），使"测量与绘制同源"
     成为结构必然而非纪律；`text/` 全程不碰平台 API（有架构测试守着）。
   - 真字形光栅化已在 R4 落地（`backend/hbft_fonts.py`，HarfBuzz + FreeType）；
     尚缺：RTL 双向文本、彩色 emoji、更完整的复杂脚本整形（属后续阶段）。
4. **布局引擎**：`BoxConstraints` 协议 + 盒子模型 + Flex（Row/Column）+ Stack
   - 采用"约束向下、尺寸向上"，替换 Phase 0 的简化实现
   - **进度（基本完成）**：`protocol.py` / `box.py` / `flex.py` / `grid.py` /
     `stack.py` / `scroll.py` 已实现，164 个无头单测全绿，覆盖率 92%，
     1001 节点**增量**布局 1.7ms（预算 5ms）、**全量**（整树标脏）17.6ms（预算 30ms）。
     尚缺：Flex 的 wrap 换行（docs/05 §4）、Grid 的对齐、以及与 core 的 RenderObject 对接。
5. **组件树**：Widget / Element / RenderObject 三层 + 脏标记 + 帧调度
   - **进度（已实现）**：`core/` 下 key / widget / element / render_object / binding
     五个模块已完成：三棵树、按"类型 + Key"复用（两轮匹配：下标 → Key）、
     StatefulWidget 生命周期、BuildOwner 帧调度（批处理 + 阶段守卫）。
     24 个测试覆盖：100 次 set_state 只重建一次、Key 正确的重排状态零丢失、
     layout 阶段改状态抛 FrameError。
   - signals（docs/06 §3 状态双轨）已在 R6.2 落地：`Signal` / `Computed` / `Effect`，
     与 Inherited 环境传播（R6.1 `ThemeScope`）共用依赖追踪内核。
   - **事件路由已在 R7.1 接上**（docs/21）：`RenderBox.hit_test` 逆序命中 +
     滚动视口裁剪，`events/pointer.py` 的三阶段路由（捕获/目标/冒泡 +
     `stop_propagation`）与 ENTER/LEAVE 命中链差分；Button 由路由驱动
     HOVER/ACTIVE/FOCUS，Input 聚焦打开 IME 通道并上报候选框。
   - **手势竞技场已在 R7.2 落地**：`events/gestures.py` 的
     `GestureArena` + Tap / DoubleTap / LongPress / Drag 按 pointer_id 竞争，
     滚动列表里的按钮"tap 或滚动"二选一（8px 阈值进令牌），输家收 cancel
     退回 ACTIVE；长按/双击时窗用注入 `time_ms` 经 `begin_frame(now_ms=)` 推进。
   - **DPI 缩放已在 R7.3 接上**：档位经 `begin_frame(dpi_scale=…)` 进帧上下文，
     `flush_paint` 在根上压等比仿射变换 → 显示列表是设备像素、帧缓冲按
     逻辑尺寸×scale；布局与命中恒为逻辑像素；`DPI_CHANGED` 下一帧生效。
     150% 黄金图（`login_form_light_150`）进 CI。
   - **样板 App「墨记」已在 R7.4 落地**（`examples/notes.py`）：侧栏导航 +
     可滚动笔记列表 + 表单 + 明暗主题切换，`ScrollView` widget 补齐，
     headless 黄金图（`notes_light` / `notes_dark`）进 CI，`--sdl2` 真窗口交互。
     它逼出并修掉文本栈一个真 bug（多字重下光栅选错字体面，见 ADR-0016）。
   - **R7.5 性能基准 → GL 决策已出**（`benchmarks/run.py`）：三场景 p95 超预算
     40–300 倍（滚动 859ms / 全屏 3125ms / 文本 576ms，预算 10ms），热点是
     软件光栅的纯 Python 逐像素 SDF。决策规则先写死、数据触发 → **启动 GL
     后端子包，范围与验收见 docs/22**（只实现现有 IR、不改 core、黄金图仍以
     软件光栅为事实源）。
   - 未实现：semantics 阶段（Phase 3 无障碍）。
6. **样式**：设计系统三层令牌（含阴影/动效令牌，规格见 `docs/13`）+ 明暗主题 + 组件变体解析
   - **进度（部分完成）**：`gfx/color.py`（含 WCAG 对比度计算）、
     `style/tokens.py`（12 级色板、8pt 间距、圆角、字号、控件高度、阴影 e0–e4、
     动效时长与缓动、语义令牌 25 个）、`style/theme.py`（明暗两版 + 取值 API）已实现。
     **明暗两版的 WCAG 2.2 AA 对比度已作为断言进测试**——颜色是我调的，对比度是算的。
   - 变体解析已完成（`variants.py` / `resolve.py`）。
   - 未完成：密度档位、高对比主题、prefers-reduced-motion。
7. **最小组件集**：Box / Text / Button / Input / Row / Column / Card
   - **进度（已完成）**：Box / Card / **Text** / Row / Column / Flexible /
     Button / Input 全部实现，配套 `style/variants.py` 的 Button 变体配方
     （6 变体 × 5 尺寸 × 8 状态）与 `style/resolve.py` 的五层确定性解析。
     一个真实登录表单能完整建出三棵树并算出正确几何（含 Flexible 撑满剩余宽度）。
   - **Text**：尺寸完全来自真实字体度量（`text_engine`），支持换行 / 对齐 /
     `max_lines` / 省略号 / 字重，`paint` 产出 `TextRunOp`。
   - **Button / Input 会渲染文字**：按钮按标签度量 + 内边距**自己算宽度**
     （不再需要调用方写 `width=80` 之类的估值），输入框显示值/占位符，
     占位符用占位符色。
   - **examples/hello.py 可运行**（`--dark` / `--deterministic`），
     并有冒烟测试保证它不腐烂。
   - 顺带修掉两个三棵树层的 bug：slot（flex 权重）穿过组件层会丢、
     重排序找不到组件型子级的 RenderObject。
8. **测试基建**：布局单测 + 黄金图测试（三平台基线）+ CI
   - **进度（基本完成）**：**1009 个无头单测全绿**（覆盖率 89.1%）；
     **12 张黄金图**（登录表单明暗与 150%、Button / Card / Input 单独、
     文本块 / 文本换行、样板 App 明暗）已建立并**按解码后的像素**逐像素比对。
     devtools 强制 `force_repaint` 让黄金图每帧都是完整画面，
     不污染生产帧的"跳干净子树"优化。
     另有**架构约束测试**（分层单向依赖 / text 层无平台 API /
     组件零硬编码——颜色与圆角·描边宽度·控件高度三类数值）。
   - **CI 已配置**（`.github/workflows/ci.yml`）：五个任务 —— lint、
     三平台测试（Windows/macOS/Ubuntu + py3.10 最低版本）、覆盖率（≥85%）、
     性能预算、架构约束。**覆盖率与性能刻意分任务**：插桩会让性能断言
     随机变红，混在一起就是假信号。黄金图不匹配时自动上传实际产物。
   - 待补：gl_backend（R7.5 基准触发，见 docs/22）、skia_backend（Phase 3）。
   - **地基整改 R1「正确性止血」已完成**（docs/14–20 系列的第一个工作包，docs/15）。
     修掉 11 处"表面正常、实际不通"的静默断链与崩溃级 bug：软件光栅宽高传反
     （宽扁画布直接崩、竖长画布底部静默少画一截）、形状裁剪 1px 越界、
     layout→paint 脏标记不联动（滚动/resize 画面不动）、滚动容器不裁剪视口、
     Flex 基线对齐交叉轴尺寸、`update` 不标 layout 脏（改几何属性静默失效）、
     复用路径丢 flex 槽位、build 无错误边界、缺"需要一帧"的调度出口、主题热切换。
     每条都带"修复前必红、修复后必绿"的回归测试：无头单测 567 → **662** 全绿；
     1001 节点**增量**布局实测 1.36ms（预算 5ms）、**全量**（整树标脏）
     17.6ms（预算 30ms）。后续 R2–R6 见 docs/16–20。
   - **地基整改 R2「测试求真」已完成**（docs/16）：让质量信号说真话。
     黄金图比对单位从 PNG 文件字节改成**解码后的 RGBA 像素**（zlib 输出不跨
     版本保证一致，"三平台逐字节相同"是侥幸）；**基线缺失 = 失败**（此前缺失
     就顺手写一份新基线然后绿灯，等于自动橡皮图章）；性能基准拆成
     **增量**与**真全量**两条并各自命名（原来的"全量 0.89ms"测的是缓存命中，
     真全量 14.6ms 是它的 16 倍——假测量比没有测量更危险）；"组件零硬编码"
     扫描从颜色扩到**圆角/描边宽度/控件高度三类数值**，清掉 4 处漏网；
     conftest 删掉两个没人用的 fixture（"时间可注入"由后端协议保证，
     不靠 fixture 装样子）。
   - **地基整改 R3「渲染协议重塑」已完成**（docs/17）：趁消费者少（只有 devtools
     与测试）把渲染层的接口形状改对。`RasterBackend` 从
     `rasterize() -> FrameBuffer` 改成**帧生命周期**
     （`begin_frame → execute(clip) → end_frame → screenshot`）——旧形状把"读回内存"
     当成后端的唯一出口，GL/Skia 在结构上无法实现它；`PositionedGlyph` 补 `y_offset`
     （HarfBuzz 四元组，R4 的前提）；录制器的"累计平移"升级为**2×3 仿射矩阵**
     （DPI 缩放与缩放动画的落点）；新增 **path 指令形状**（扁平 verb 数组，
     `icons/` 的载体）；软件光栅加**不透明对齐矩形的整行切片快路径**，
     1280×800 全屏填充 **749ms → 3ms**（预算 50ms）。

### 验收标准（DoD）

- [x] 三平台 CI 全绿（Windows / macOS / Ubuntu），含 headless 黄金图
      —— **首次推送即全绿**（8 个任务）。黄金图在三平台**像素级一致**
      （R2 之后比对单位是解码后的 RGBA，见 AGENT.md 的 CI 段），
      说明"确定性"不只是设计意图，而是可验证的事实。
- [ ] 中文输入：可输入、可删除、光标位置正确（组合态渲染可留到 Phase 2）
      —— R7.1 已接通"点击聚焦 + IME 通道 + 候选框位置上报"；
      编辑模型（文本 / 选区 / 组合态）仍是空白，属 Phase 2 首项
- [ ] 100%/125%/150% 缩放下无模糊、无错位
- [x] 布局引擎 100% 无窗口可测，单测覆盖 ≥ 85%（实测 89%）
- [ ] 样板 App（一个真实小工具）在三平台可用
      —— R7.4 已交付 `examples/notes.py`（headless 出图 + SDL2 路径）；
      SDL2 真机交互尚待在 Windows/macOS/Linux 上各验一遍
- [ ] 帧时间 p95 < 8ms（200 节点界面）
      —— 软件光栅实测 p95 超预算两个数量级（R7.5），已按规则启动 GL 后端子包
      （docs/22）；该项由 GL 后端达成后勾选

### 主要风险

- SDL2 在 macOS/Wayland 上的打包与二进制分发
- 文本度量与渲染后端度量不一致 → 用同一个度量源，禁止两套
- Python 帧开销 → 先量化，不做过早优化

---

## Phase 2 · 可用（Usable）

**目标一句话**：内部团队愿意用它做生产工具。

### 交付

1. **IME 完整支持**：组合态下划线、候选框跟随光标（Windows/macOS/Wayland 三套实现）
2. **行为原语 primitives**：portal / overlay_manager / focus_trap / roving_focus / popper / dismissible / presence / scroll_lock —— 弹层与菜单的专业地基（对应 shadcn 的 Radix 层）
3. **设计系统 v1 落地**：`docs/13` 的令牌 / 阴影 / 动效 / 组件配方规范进入代码；"组件零硬编码"审查接入 CI
4. **滚动与裁剪**：ScrollView、滚动条、裁剪、锚点保持
5. **表单全家桶**：TextArea / Select / Checkbox / Radio / Switch / Slider / Form + 校验
6. **导航**：AppBar / Sidebar / Tabs / Drawer / Menu / 命令系统 + 快捷键
7. **反馈**：Dialog / Toast / Tooltip / ContextMenu / Popover（全部由 primitives 组合而成）
8. **数据展示**：List / Tree / Table（含虚拟滚动）/ Badge / Progress / Empty / Skeleton
9. **图标系统**：Lucide 风格 SVG path 注册表 + 描边渲染，常用 200 个
10. **焦点系统**：Tab 顺序、焦点作用域、焦点环
11. **开发者工具**：组件检查器 + 热重载

### 验收标准（DoD）

- [ ] 三个平台的中文输入法行为一致，候选框跟随光标
- [ ] 所有交互控件的高度 / 间距 / 圆角 / 颜色 100% 来自设计令牌（CI 扫描零硬编码）
- [ ] 10 万行表格滚动 p95 < 16.6ms
- [ ] 完整键盘可达（不使用鼠标可完成样板 App 的全部操作）
- [ ] 组件 Tier 0–2 全部达到"生产可用"
- [ ] 黄金图场景 ≥ 120 个

---

## Phase 3 · 专业（Professional）

**目标一句话**：可以对外发布 1.0，陌生人敢用在商业软件里。

### 交付

1. **无障碍**：语义树 + Windows UIAutomation / macOS NSAccessibility / Linux AT-SPI 桥接
2. **高级组件**：SplitPane / 拖拽 / 日期时间选择 / 颜色选择 / 文件选择 / 图表 / Markdown / 代码高亮
3. **Skia 光栅后端**：作为高质量默认后端，自研 GL 转为兜底
4. **文本进阶**：富文本、选区、复制粘贴、双向文本（RTL）、emoji
5. **动效引擎**：`motion/` 包（缓动 / 弹簧物理 / 过渡）+ 可中断、可降级（prefers-reduced-motion 全局生效）
6. **性能**：脏区域重绘、层缓存、布局 profiling 工具
7. **打包分发**：`.exe`（签名）/ `.app`（公证）/ `.AppImage` + `.deb`；`inkstone build` 一键产物
8. **文档站**：教程、API 参考、组件示例、迁移指南

### 验收标准（DoD）

- [ ] 读屏软件可朗读主要控件（三平台各一款：NVDA / VoiceOver / Orca）
- [ ] 对比度达到 WCAG 2.2 AA
- [ ] 1000 节点界面交互 p95 < 16.6ms
- [ ] 三个平台都能产出可安装、可运行、已签名的独立应用
- [ ] 公开 API 冻结，发布 1.0.0，提供 12 个月 LTS

---

## Phase 4 · 生态（Ecosystem）

- 设计工具联动（Figma 令牌 → `tokens.py` 双向同步）
- 组件市场与模板
- 插件机制与第三方组件规范
- 声明式协议（spec）独立成标准，支持非 Python 客户端生成界面
- 移动端可行性预研（不在 1.x 承诺范围）

---

## 时间与投入（诚实估计）

假设"一个人 + AI 辅助主力开发"：

| 阶段 | 独立开发者（业余） | 3–4 人小组 |
|---|---|---|
| Phase 1 | 3–4 个月 | 6–8 周 |
| Phase 2 | 4–6 个月 | 2–3 个月 |
| Phase 3 | 6–9 个月 | 3–4 个月 |
| **到 1.0 合计** | **12–18 个月** | **6–9 个月** |

AI 在这里加速的是**确定性劳动**（布局算法、组件样板、测试生成、文档），
**不能**加速的是平台差异的调试、IME 这种需要真机反复试的东西、以及设计决策本身。

---

## 明确的"不做"（1.x 范围外）

- 移动端（iOS / Android）
- 浏览器后端（WebAssembly）
- 游戏引擎级渲染（3D、粒子、着色器编排）
- 与 Qt/GTK 的互操作
- 主题市场、付费组件

写在这里是为了避免"顺手做一下"把项目拖垮。

---

## 检查点（Go / No-Go）

每个阶段结束必须回答三个问题，答不上来就停：

1. 上一阶段的 DoD 是否**全部**达成（不是"基本达成"）？
2. 样板 App 是否真的更好用了，而不是更麻烦了？
3. 有没有哪个技术债已经大到必须现在还，否则后面翻倍？
