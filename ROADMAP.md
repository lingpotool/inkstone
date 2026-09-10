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
   - 窗口生命周期、DPI 报告、光标、剪贴板
   - 事件泵与事件归一化
2. **渲染管线**：显示列表 + 自研 GL 光栅后端（先不引入 Skia，减少变量）
   - 矩形 / 圆角矩形 / 描边 / 文本 / 裁剪
3. **文本**：字体加载与度量 + 单行/多行排版 + **CJK 字体回退链**
4. **布局引擎**：`BoxConstraints` 协议 + 盒子模型 + Flex（Row/Column）+ Stack
   - 采用"约束向下、尺寸向上"，替换 Phase 0 的简化实现
   - **进度（基本完成）**：`protocol.py` / `box.py` / `flex.py` / `grid.py` /
     `stack.py` / `scroll.py` 已实现，164 个无头单测全绿，覆盖率 92%，
     1001 节点全量布局 0.89ms（预算 5ms）。
     尚缺：Flex 的 wrap 换行（docs/05 §4）、Grid 的对齐、以及与 core 的 RenderObject 对接。
5. **组件树**：Widget / Element / RenderObject 三层 + 脏标记 + 帧调度
   - **进度（已实现）**：`core/` 下 key / widget / element / render_object / binding
     五个模块已完成：三棵树、按"类型 + Key"复用（两轮匹配：下标 → Key）、
     StatefulWidget 生命周期、BuildOwner 帧调度（批处理 + 阶段守卫）。
     24 个测试覆盖：100 次 set_state 只重建一次、Key 正确的重排状态零丢失、
     layout 阶段改状态抛 FrameError。
   - 未实现：signals（docs/06 §3 状态双轨）、semantics 阶段（Phase 3 无障碍）。
6. **样式**：设计系统三层令牌（含阴影/动效令牌，规格见 `docs/13`）+ 明暗主题 + 组件变体解析
   - **进度（部分完成）**：`gfx/color.py`（含 WCAG 对比度计算）、
     `style/tokens.py`（12 级色板、8pt 间距、圆角、字号、控件高度、阴影 e0–e4、
     动效时长与缓动、语义令牌 25 个）、`style/theme.py`（明暗两版 + 取值 API）已实现。
     **明暗两版的 WCAG 2.2 AA 对比度已作为断言进测试**——颜色是我调的，对比度是算的。
   - 未完成：组件令牌层、变体解析（`variants.py` / `resolve.py`）、
     密度档位、高对比主题、prefers-reduced-motion。
7. **最小组件集**：Box / Text / Button / Input / Row / Column / Card
   - **进度（除 Text 外已实现）**：Box / Card / Row / Column / Flexible / Button / Input
     已完成，配套 `style/variants.py` 的 Button 变体配方（6 变体 × 5 尺寸 × 8 状态）
     与 `style/resolve.py` 的五层确定性解析。一个真实登录表单能完整建出三棵树
     并算出正确几何（含 Flexible 撑满剩余宽度）。
   - Text 未实现：**文字宽度不许估算**，必须等 `text/` 的字体度量落地（docs/04 反复强调的坑）。
   - 顺带修掉两个三棵树层的 bug：slot（flex 权重）穿过组件层会丢、
     重排序找不到组件型子级的 RenderObject。
8. **测试基建**：布局单测 + 黄金图测试（三平台基线）+ CI
   - **进度（部分完成）**：282 个无头单测全绿；5 张黄金图（登录表单明暗、
     Button / Card / Input 单独）已建立并逐字节比对。
     devtools 强制 `force_repaint` 让黄金图每帧都是完整画面，
     不污染生产帧的"跳干净子树"优化。
   - 待补：CI 配置、gl_backend / skia_backend、Text 渲染。

### 验收标准（DoD）

- [ ] 三平台 CI 全绿（Windows / macOS / Ubuntu），含 headless 黄金图
- [ ] 中文输入：可输入、可删除、光标位置正确（组合态渲染可留到 Phase 2）
- [ ] 100%/125%/150% 缩放下无模糊、无错位
- [ ] 布局引擎 100% 无窗口可测，单测覆盖 ≥ 85%
- [ ] 样板 App（一个真实小工具）在三平台可用
- [ ] 帧时间 p95 < 8ms（200 节点界面）

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
