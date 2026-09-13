# HANDOFF —— 交接文档

> 写给下一个接手的对话。读完这份 + `AGENT.md` + `ROADMAP.md`，就能直接开工。
> 交接时间：**2026-09-13** · 地基整改 R1–R6 + 收官包 R7 + GL 子包 R8 + R9–R11 **全部完成** ·
> 无头单测 **1152 个全绿**（覆盖率 89.93%）· 黄金图 12 张像素级比对
>
> **当前主线**：**GL 后端子包（`docs/22`）已完成**——R7.5 的性能基准触发了预先写死的
> 决策规则（三场景 p95 超 10ms 预算 40–300 倍）。R8.1 接缝+逻辑层、R8.2
> Windows WGL 真机驱动、R8.3 热路径+合批+图集+帧去重、R8.4 状态指令 +
> 滚动 repaint boundary + GL 层缓存、R8.5 SDL2 可选依赖、R8.6 真窗口上屏均已完成。
> **R7.5 验收达成**：本机 RTX 3060 / GL 4.6 实测 p95 全屏 **4.4ms** /
> 文本 **8.6ms** / 滚动 **1.0ms**，三场景全部 ≤10ms（软件光栅仍是黄金图
> 的确定性事实源，未变）。**R12.2 后进一步降到 p95 全屏 ~2.2 /
> 文本 ~1.6 / 滚动 ~0.6ms**（预计算已定位字形，见下）。
>
> **R9 焦点/文本编辑 + R10 窗口身份与能力也已完成**（ADR-0022）：原生边框 +
> 平台化外观（任务栏身份 / 程序化图标 / DWM 标题栏配色 / 最小尺寸），
> 样板 App 真窗口路径已接线。
>
> 更早的分包更新记录已归档为 git 历史；本文只描述**当前真实状态**。
> 规矩：本文数字与"已完成"必须可复现；发现过期就改，不留"看起来还行"的旧描述。

---

## 一、这是什么项目

**inkstone** —— 专业的跨平台 Python 原生 UI 系统。纯 Python、自绘渲染、
Windows / macOS / Linux 一等公民、中文一等公民。

- 仓库：`https://github.com/lingpotool/inkstone`（`main` 分支，直接推）
- 设计文档在 `docs/`：`00–13` 是规格书，`14–22` 是整改与收尾系列
  （`14` 施工总纲含执行协议与已拍板决策，**动手前先读**）
- `AGENT.md` 是规则书（**含 ADR 表，务必先看**），`ROADMAP.md` 是推进顺序

**用户的合作偏好**：

- "你只要保证架构正确，专业就行" —— 授权全权决策，但**架构必须站得住**
- "按照整个项目的推进顺序，别搞乱了" —— **严格按 ROADMAP 推进**，别被单点带偏
- 用户会真的看渲染图，并给观感反馈（锯齿、圆角缺口都是他先发现的）

---

## 二、当前状态

### 已完成（与代码一致）

| 模块 | 内容 |
|---|---|
| `layout/` | 协议 / 盒子 / Flex / Grid / Stack / Scroll，无窗口可测，覆盖 92% |
| `core/` | 三棵树 + 帧调度（批处理、阶段守卫）+ 环境传播（`InheritedWidget` / `ThemeScope`）+ signals + 指针路由与手势竞技场的宿主（`dispatch_pointer`）+ DPI 档位 |
| `backend/` | 归一化事件（window_id / 真时间戳 / IME 双通道）+ 无头后端 + SDL2 + **跨平台真字体引擎 HarfBuzz + FreeType** + 内嵌 Inkstone Sans 兜底 |
| `style/` | 三层令牌 + 明暗主题 + 变体解析（含手势阈值 `tap_slop`/`long_press_ms`/`double_tap_ms`） |
| `text/` | 完整文本栈：度量 / CJK 回退链 / 整形 / 断行（含禁则）/ 段落排版；face_key 保证字形与整形同一个字体面（ADR-0016） |
| `widgets/` | Box / Card / Text / Row / Column / Flexible / **ScrollView** / Button / Input |
| `gfx/` | 显示列表（`TextRunOp` / path 指令）+ 仿射录制器 + 软件光栅（帧生命周期 + 快路径）+ PNG |
| `devtools/` | 确定性截图 + **12 张黄金图** + 字形源自动配对 + DPI 档位 |
| `events/` | `ime.py`（组合态模型）、`pointer.py`（命中链三阶段路由）、`gestures.py`（手势竞技场） |
| `examples/` | `hello.py`（组件示例）+ **`notes.py`「墨记」样板 App**（侧栏 + 滚动列表 + 表单 + 明暗主题；headless 黄金图 + `--sdl2`），都进 CI 冒烟 |
| `benchmarks/` | `run.py`：长列表 / 全屏 / 文本密集三场景帧耗时 p50/p95（GL 决策依据） |
| CI | 三平台测试 + 覆盖率 + 性能 + 架构，五个任务（8 个 job） |

### R7 的 5 个提交（时间序）

```
2580a88 feat(events): R7.1 命中测试与指针事件路由
4e5c63f feat(events): R7.2 手势竞技场
cb76c17 feat(core):   R7.3 DPI 缩放接线
873d1df feat(examples): R7.4 样板 App「墨记」+ 修复多字重光栅选错字体面
5bbb29e perf(benchmarks): R7.5 性能基准 → 数据触发 GL 后端子包
```

### ROADMAP Phase 1 进度

- ① 平台抽象 ✅ · ② 渲染管线 **部分**（软件光栅可用；**GL 后端已立项 docs/22**）·
  ③ 文本 ✅ · ④ 布局 ✅ · ⑤ 组件树 ✅ · ⑥ 样式 ✅ · ⑦ 组件 ✅ · ⑧ 测试与 CI ✅

**Phase 1 的 DoD 状态（诚实版）**：

- [x] 三平台 CI 全绿（`77fed3a` 首次推送即全绿），黄金图像素级一致
- [x] 中文输入：可输入、可删除、光标位置正确
      —— R9 落地焦点系统 + 编辑模型（text/selection/composition、字素簇移动
      删除、剪贴板、候选框跟随光标）；R11 修 DPI 后真机驱动 IME 输入中文并
      截图确认（候选框跟随光标）。更复杂的组合态高亮属 Phase 2 打磨。
- [x] 100%/125%/150% 缩放（R7.3 + R11）：逻辑像素布局、物理=逻辑×scale、
      文字在物理分辨率重光栅化；150% 黄金图已肉眼核对。R11 修掉"进程 DPI
      不感知导致 Windows 位图拉伸整窗"（125% 屏实测 1:1，不再发虚）。
- [x] 布局引擎 100% 无窗口可测，覆盖 ≥85%（实测 89.93%）
- [x] **样板 App 在 Windows 上可用**（真机验收清单见下）；macOS/Linux 待适配 ⬜
- [x] 帧时间 p95 < 8ms（R12.2 后本机实测，p95：滚动 0.5–0.8 / 全屏 1.8–2.6 /
      文本 1.5–1.8ms；场景定义未动）

### Phase 1 Windows 真机验收清单（R12.3，逐项跑过）

| 项 | 结果 |
|---|---|
| 点按钮 / 切筛选（全部·收藏·归档） | ✅ 高亮跟随，列表内容切换 |
| **鼠标滚轮滚动** | ✅ R12.1 补上；实测滚 5 格内容位移 240 逻辑像素 |
| 输入框中文输入 + 候选窗 | ✅ 搜狗候选窗出现、空格上屏中文（ADR-0024） |
| 输入框英文输入 / 选中 / 删除 / 剪贴板 | ✅ R9 编辑模型 |
| 切主题（含标题栏 DWM 跟随） | ✅ 暗色标题栏随主题变 |
| 窗口缩放 / 最大化 / 贴靠 | ✅ 最大化到 1920×991 后布局重排，无拉伸无黑边；原生边框保留（snap 可用） |
| 任务栏身份与图标 | ✅ 已接线（AppUserModelID + 程序化图标）——观感需人眼确认 |
| 关闭 | ✅ 关窗消息正确处理 |

---

## 三、下一步（按 ROADMAP 顺序，别跳）

### ① GL 后端子包（`docs/22`）—— 已完成

R7.5 的规则先写死（p95 > 10ms），实测三场景 p95 = 859 / 3125 / 576ms，
热点是软件光栅的纯 Python 逐像素 SDF 与"整份显示列表全量光栅"。
docs/22 已定范围：**只实现现有显示列表 IR 的指令集**、沿用 R3.1 帧生命周期、
**不改 core**、不引第二套文本栈（字形仍来自 `HbFtFontEngine`）、
黄金图仍以软件光栅为事实源、不做 Skia。验收：三场景 p95 ≤ 10ms（参考机）。

- **R8.1 已完成**：`GLDriver` 驱动接缝 + `GLRasterBackend` 逻辑层
  （帧状态机、op.clip×脏矩形取交→scissor、半径钳制、文本取掩码/连字、
  IME 下划线、纹理生命周期、读回校验）。无 GPU 也能测——`FakeDriver`
  记录调用序列，30 例全绿；取字形规则 `glyph_mask_plan` 两后端共用。
- **R8.2 已完成（Windows）**：`backend/gl_wgl.py` ctypes 直调 WGL——
  隐藏窗口 + 离屏 FBO（**不依赖 SDL2**）；SDF 着色器画实心/圆角/描边，
  `GL_RED` 纹理画字形，`glReadPixels` 读回并翻转行序；真实 GPU 测试 9 例
  （非 Windows skip）。实测（RTX 3060 / GL 4.6）：全屏 p95 3125→34ms、
  文本 576→109ms、滚动 859→203ms。踩掉两个真 bug：扩展入口必须在
  上下文 current 之后加载；字形纹理按 `id(mask)` 缓存会每帧重传（内置
  provider 每次新建对象），改内容键 + FIFO。
- **R8.3 已完成**：热路径缓存（段落排版结果 / 逐字符脚本判定，文本页纯 Python
  37→6.9ms）；GL 矩形顶点属性合批 + 2048² 字形图集；帧去重（内容与上一帧逐指令
  相同就整帧跳过，damage 思想的最简形态）。
- **R8.4 已完成（ADR-0019）**：显示列表加**状态指令**（`PushTranslateOp` /
  `PushClipOp` / `PopOp`，`resolve_state_ops` 保证与"烘焙版"逐像素等价，
  两后端共用、黄金图未动）；**滚动成为 repaint boundary**（子树录成内容局部
  坐标的层缓存，滚动只改平移，纯 Python 21ms→0.10ms）；**GL 层缓存**
  （`PushLayerOp` → 离屏 FBO 纹理，命中即复用，每帧只画一个四边形）。
  顺带修掉"自动清屏擦掉未变区域"的真问题：光栅后端不自动清屏，背景由显示
  列表指令负责，脏子树重绘保留区域外像素。
- **SDL2 获取已专业化（ADR-0020，R8.5）**：不往仓库塞二进制；可选依赖
  `inkstone[sdl2] = pysdl2 + pysdl2-dll`（三平台预编译库），加载优先级
  环境变量 > 可选依赖 > 系统库。本机已装并在**真窗口**上验证：建窗、DPI、
  事件泵、光标、IME 通道、剪贴板全通。过程中撞到并修掉两个潜伏 bug：
  `SDL_CreateWindow` 指针返回值未声明 `restype` 被截断成 32 位（访问违例）、
  DPI 函数签名缺失；签名现集中在 `_bind_signatures`。
- **R8.6 真窗口 GL 上屏已完成**（ADR-0021）：`WindowSpec(opengl=True)` + 
  `sdl_gl_driver(backend, window)` 把驱动挂到 SDL 的 GL 上下文；绘制仍进离屏
  FBO，`end()` blit 到默认帧缓冲 + `SDL_GL_SwapWindow`。Windows 真机验证
  （红块位置/底色正确、`gl_err=0`、可连续换链）；`examples/notes.py --sdl2`
  已改走 GL。踩坑：挂载模式下 `begin()` 无条件 `wglMakeCurrent(0,0)` 会把
  SDL 上下文解绑（FBO 校验静默返回 0）。
### ② 焦点/文本编辑（R9）与窗口身份能力（R10）—— 已完成

- **R9 焦点系统 + 文本编辑已完成**：`events/focus.py`（唯一焦点 / focus-visible /
  Tab 遍历 / 点空白失焦）+ Input 编辑模型（text + selection + composition、
  字素簇移动删除、光标/选区/组合态渲染、剪贴板、候选框跟随）；core 增加
  `dispatch_key/text/ime`，App 主循环已接。顺带修掉"点过的控件永久带焦点环"
  （鼠标来源不算 focus-visible）。Phase 1 的"中文输入"DoD 达成。
- **R10 窗口身份与能力已完成**（ADR-0022，`backend/windows_shell.py` +
  `Backend` 协议扩展）：`set_app_identity`（任务栏身份，建窗前设）/
  `set_title` / `set_min_size` / `set_maximized` / `set_fullscreen` /
  `set_window_theme`（DWM 深色 + 标题栏底色，**保留原生边框** → Snap
  Layouts / 贴靠 / 无障碍全在）/ `set_icon`。图标**不在仓库**：
  `inkstone.app.app_icon_rgba` 用自家显示列表 + 软件光栅画（圆角方石 +
  环形砚池 + 墨点），逐字节确定，`tools/build_icon.py` 打包时导出 .ico。
  `examples/notes.py --sdl2` 已接身份/最小尺寸/图标/主题跟随，且窗口可缩放
  （视口跟 `WINDOWEVENT_RESIZED` 走，原生 snap 因此可用）。踩坑：
  `SDL_SysWMinfo` 必须照实声明+留余量（SDL 整段写入，只声明 HWND 会栈越界）。
- **R11 真机观感修复已完成**（ADR-0023）：用户实测反馈"整个界面发虚、点按钮
  位置偏移、中文输入法不工作"。三个症状同一个根：**坐标单位与 DPI 感知**。
  ① 发虚：进程 DPI 不感知，Windows 在 125% 屏上把整窗位图拉伸 1.25×（实测
  物理 1920×1080，进程只见 1536×864 虚拟桌面）→ 建窗前设 SDL 的
  `SDL_WINDOWS_DPI_AWARENESS=permonitorv2`。② 点击偏移：感知之后 SDL 给的
  指针坐标是物理像素，命中测试却是逻辑像素 → 新增 `_to_logical()` 统一换算
  （`RESIZED` 宽高同理，`begin_frame` 也修了给物理尺寸导致的二次放大）。
  ③ 输入法：**千万别在建窗前先调 Win32 的 `SetProcessDpiAwarenessContext`**
  ——实测会让 IME 组合事件扣到上屏才吐；交给 SDL 的 hint 设就正常（A/B 实测；
  Win32 那条只留作老 SDL 兜底）。修后真机实测：客户区 950×650 物理 =
  760×520 逻辑 × 1.25（1:1 清晰）；点「深色主题」准确换主题。
- **R11.2 中文输入（搜狗）修复完成**（ADR-0024）：用户实测"中文打得出但搜狗
  候选窗不出"。查 SDL 源码（`SDL_windowskeyboard.c`）找到根因：SDL 默认
  **UI-less 模式**，`WM_IME_SETCONTEXT` 把 `*lParam = 0`，主动关掉 IME 自己的
  窗口；微软拼音自带候选窗所以看不出来，搜狗/QQ/百度把预选界面画在 IME 窗口
  里就什么都不显示。修法：初始化时 `SDL_IME_SHOW_UI=1`（须在首次
  `start_text_input` 前，`IME_Init` 只读一次）。真机全屏截图确认：拼音 →
  搜狗候选窗（`zh'ong` + 候选列表）→ 空格上屏中文。
- **R11.3 小字发虚修复完成**：GL 字形落位没对齐设备像素（125% 屏上笔位几乎
  总是小数），图集 `GL_LINEAR` 把每个字形重采样一遍 → 小字笔画忽粗忽细。
  修法：`draw_glyph` 对齐到整设备像素。打像素放大对比：按钮/正文从"发丝"
  变为实心均匀。更优雅的亚像素相位光栅化列为后续优化。
- **R12.1 滚轮滚动完成**：此前全库没有任何代码读 `wheel_dx/wheel_dy`——滚动
  只有拖拽一条路，桌面的滚轮主路径整条漏掉（Phase 1 用程序化偏移验证过，
  漏了真机口径）。修法：走 `handle_pointer_event` 这条非手势输入缝，只在
  TARGET/BUBBLE 阶段消费（内→外），**偏移没变就不叫停传播**，嵌套滚动因此
  结构成立；步长进令牌 `gestures["wheel_step"]=48`。
- **R12.2 文本帧时间达标**：文本场景每帧重建约 2400 个字形对象是热点。
  `ShapedLine` 构造时预计算已定位字形并缓存，`positioned_glyphs()` 只读。
  顺带让显示列表去重比较走元组同一性短路。实测文本 p95 7.1–10.7 → 1.5–1.8ms，
  全屏 4.5–4.9 → 1.8–2.6ms。**12 张黄金图逐字节不变**（零视觉变化）。
- **R13.1 滚动条完成**（ADR-0026）：第一版做成"常驻半透明覆盖条"，用户一眼
  看穿——它盖在条目上。改为**让出槽位**（`overflow: auto` 语义）：只有真溢出
  才从内容区扣掉 `thickness+margin` 再量一次（有界的一次二次布局，滚动本身
  不触发布局）。令牌新增 `scrollbar` 几何组 + 语义色 `scrollbar`/`scrollbar-hover`。
- **R13.2 滚动条拖拽完成**：新增 `HandleDragRecognizer`（把手拖拽，将来
  splitter/滑块同用）。**按下点不在把手上时用 hold 退场**——直接 reject 会让
  内容拖拽在 DOWN 当场获胜、绕过 8px slop 门槛（ADR-0014 的 bug）。
- **顺带修掉一个真机才暴露的老 bug**：`translate_motion` 用 `raw.which` 当
  pointer_id、`translate_button` 留 0 → 竞技场对不上号，**鼠标拖动列表在真机上
  一直是失效的**（单元测试两端都手写 0，同错自洽所以全绿）。统一为
  `_pointer_id_of`，并补回归测试。
- **R14.1 动效基座（`motion/` 不再是空壳）**：`Ticker`（排帧；跑完自动退出集合，
  **空闲不留帧**）+ `AnimatedValue`（标量过渡，改目标从当前值起步不跳变）+
  `prefers-reduced-motion`（读 Windows"辅助功能→动画效果"，减少动效时时长归零）。
  时间仍只有 `begin_frame(now_ms)` 一条来源，所以动画在无头测试里完全确定。
- **R14.2 滚动条改覆盖式 + 自动隐藏**（ADR-0026 v2，用户拍板 Chromium/Flutter
  路线）：撤掉槽位让位，内容回到整视口排版；活动时亮起、闲置 900ms 后 250ms
  淡出；悬停/拖拽变粗（10→14px）。两个真机踩出来的坑：①可见性必须**每帧查
  路由的 hover 链**——指针移出容器后收不到事件，靠事件置位的标志永远清不掉，
  条就不淡出；②淡出要**从截止时刻起算**，否则掉帧会让时长取决于帧率。
- **R14.3 轨道翻页 + 双轴转角**：点轨道翻 90% 视口（留 10% 重叠跟读）；
  双轴时两条轨道各让开对方、右下角补方块。条不可见时点轨道不响应。
- **仍未做**：窗口位置/尺寸记忆（应用外壳层）；**键盘滚动**（需要 ScrollView
  可聚焦，属焦点系统的扩展）、Shift+滚轮横向（等 BOTH 容器有消费者）、
  锚点保持 / 过滚动 / 虚拟滚动（Phase 2）；
  Linux GLX/EGL、macOS CGL；
  路径三角化；通用脏矩形损伤跟踪；应用外壳脏区调度；macOS 的 NSWindow
  appearance 接线（`windows_shell` 目前只有 Win32 实现，其他平台是安全空操作）。
- Linux GLX/EGL、macOS CGL 驱动照 `GLDriver` 协议补（Windows 已通过 WGL +
  SDL 上下文两条路验证）；跨平台窗口获取见 ADR-0020。

### ③ Phase 1 已收口（Windows）→ 下一步是 Phase 2

Phase 1 的 6 项 DoD 在 Windows 上全部达成（验收清单见第二节）。下一段主线按
ROADMAP 走 Phase 2「可用」，建议顺序：

1. ~~滚动条~~ **已完成（R13.1/R13.2）**；接着做**虚拟滚动 + 锚点保持**
   （大列表的必备件，与数据展示组件同批）；
2. **primitives**（portal / overlay_manager / focus_trap / popper / dismissible /
   presence / scroll_lock）——弹层与菜单的地基；
3. 反馈组件（Dialog / Toast / Tooltip / ContextMenu）→ 表单全家桶 → 数据展示
   （List / Tree / Table）。

### ④ 跨平台工作流（独立，Windows 完成后做）

ADR-0025 拍板：macOS/Linux 适配独立成工作流，不阻塞 Windows 开发。

- R4 已用**同一套 HB+FT**取代三平台原生引擎，所以不是"再写两份引擎"，而是在
  真机上验证系统字体目录扫描、回退链命中、内嵌兜底字体接管（零系统字体时中文
  不出豆腐块）。接口（`MetricsProvider` + `GlyphProvider`）固定。
- GL 驱动照 `GLDriver` 协议补 Linux GLX/EGL 与 macOS CGL（Windows 已通过 WGL +
  SDL 上下文两条路验证）。
- 逐项复跑 Windows 那份真机验收清单（点击、滚轮、中文候选窗、缩放、DPI 迁移）。
- 平台专有外观（DWM 那套）要各写一份：macOS NSWindow appearance、Linux 走 GTK
  设置或自绘标题栏；`windows_shell` 已有"非 Windows 安全空操作"的形状可照搬。

---

## 四、验证命令

```bash
cd /e/inkstone
./.venv/Scripts/python.exe -m pytest tests -q          # 1152 个必须全绿
./.venv/Scripts/python.exe -m ruff check src tests examples benchmarks
./.venv/Scripts/python.exe -m ruff format --check src tests examples benchmarks
./.venv/Scripts/python.exe -m mypy                     # strict，零错误
./.venv/Scripts/python.exe -m pytest tests -q -m "not slow" \
    --cov=src/inkstone --cov-fail-under=85             # 覆盖率（89.93%）
# 或一把梭：make check
```

- 更新黄金图：`INKSTONE_UPDATE_GOLDEN=1 pytest tests/unit/test_golden_form.py tests/unit/test_text_render.py`
  → **更新后必须肉眼打开 PNG 确认**，再正常模式复跑确认绿。
- **性能/帧耗时测试必须无插桩单独跑**（`-m "slow"`；覆盖率插桩会让墙钟断言随机变红）。
- 帧耗时基准（不进 CI，手动）：`python benchmarks/run.py`（可 `--json`）。

---

## 五、已知问题与技术债（诚实清单）

1. **软件光栅不是生产帧率后端**：三场景 p95 超预算 40–300 倍（R7.5）。
   它是黄金图的确定性事实源，帧率问题由 GL 后端（docs/22）解决。
2. **文本编辑模型不存在**（Phase 2 首项）：输入框不能输入/删除/移动光标。
3. **没有通用单子容器**（Container / SizedBox）：无法固定宽高 + padding 包一个子级；
   样板 App 的侧栏宽度只能靠 `Flexible(flex=)` 比例绕开。
   `layout.RenderContainer` 已具备能力，缺 widgets 层包装。（docs/20 §R6.4）
4. **`app.py` 仍是占位桩**：SDL2 帧循环目前暂住在 `examples/notes.py`，
   应在"应用外壳"落地时下沉为 app 层。
5. **彩色 emoji 是灰度轮廓**（FreeType 灰度抗锯齿，不画 COLR/CBDT）。
   宽度与位置正确，排版不受影响。
6. **内嵌兜底字体覆盖 GB2312 6763 字 + 拉丁/希腊/西里尔/假名**：生僻字与韩文
   不在覆盖内（口径与加档方法见 `tools/build_embedded_font.py`）。
7. **架构上有 4 处已登记的反向依赖**（`test_architecture.py` 的 `KNOWN_EXCEPTIONS`）：
   gfx/text → layout（几何原语属共享内核）、backend → gfx、core → style。
   登记表不允许留失效条目，消除了就要删掉。
8. Flex 还不支持 `wrap` 换行（docs/05 §4）。滚动条、锚点保持、过滚动属 Phase 2。
9. `.gitattributes` 声明 `eol=lf` 但工作区多为 CRLF，三平台 CI 会产生幽灵 diff。
   修法：`git add --renormalize .`（大 diff，单独提交）。
10. **待用户拍板**：`docs/13 §8` 写"… → 状态 → 实例覆盖"，
    `style/resolve.py` 把**交互态放在最后**。改回来只需对调两行。

---

## 六、这一程积累的硬规矩（AGENT.md 有完整版）

最容易被踩的几条：

1. **文字宽度只度量，不估算。** 一切经 `Backend.measure_text`。
2. **按字素簇操作，不按码点。** 断行/选区/省略号/脚本分段先过 `grapheme_clusters()`。
3. **渲染层按 `glyph.x` 画，不自己累加 advance**；字形必须画进 advance 宽度里。
4. **`RenderObject` 拿不到 `BuildOwner`**，环境服务一律"元素写、渲染对象读"；
   挂载时只调 `create_render_object`，字段必须在 create 里填满。
5. **手势识别器由组件层（能读令牌）创建，经元素写进渲染对象**；
   组件不许直接解释裸 DOWN/UP 判点击——那是竞技场的裁决权（ADR-0014）。
6. **命中链 target 优先、三阶段分发**；滚动裁剪由"视口 bounds 先于递归"结构成立（ADR-0013）。
7. **渲染问题必须打像素看**；**先量化确认问题存在，再动手改**（R7.5 就是这条的产物）。
8. 提交信息用中文，说清"为什么"，格式参考 `git log`。

---

## 七、环境速查

- Python venv：`E:\inkstone\.venv`（Python 3.13，Windows）
- 命令一律从 `cd /e/inkstone` 开始，用 `./.venv/Scripts/python.exe`
- 依赖：运行时为 HB+FT 文本栈（`uharfbuzz` / `freetype-py` / `fonttools`）；
  PNG 编码是 stdlib 手写，无其它运行时依赖
- 临时产物放 `.private/`（已 gitignore）；黄金图失败产物在
  `tests/golden/failures/`（CI 失败时上传为 artifact）
- 原型 `prototype/` 已冻结，不参与打包

---

## 八、开工姿势（建议）

1. 读 `AGENT.md`（**重点看 ADR 表**）→ `ROADMAP.md` → 本文档
2. 跑一遍验证命令确认起点全绿（1152 passed / mypy 干净 / 覆盖 89.93%）
3. 跑一次 `python examples/notes.py` —— 看当前最完整的界面长什么样；
   `--sdl2` 看真窗口（图标 / 标题栏配色 / snap）。
4. 按第三节顺序推进：①②（GL 子包、焦点编辑、窗口身份）**均已完成**，
   Phase 1 在 Windows 上收口 → 下一段是 ③Phase 2（先做滚动条与虚拟滚动），
   ④跨平台工作流等到 Windows 做透之后（ADR-0025）
5. 每完成一块：全量检查全绿 → commit（中文说明为什么）→ push

地基是结实的：三棵树、令牌、跨平台文本栈、确定性渲染、事件/手势/DPI 闭环、
无头测试链路、CI 全通。界面**能点、能滚、能打字（含中文候选窗）、能切主题、
能缩放**，帧时间 p95 已进 8ms——Phase 1 的 DoD 在 Windows 上全部达成。
下一步是"把它变成能拿去做产品的东西"：组件面（Phase 2）与跨平台适配。

---

## 九、唯一的上游噪音

CI 有一个非阻塞告警：`actions/checkout@v4` 与 `actions/setup-python@v5`
仍以 Node.js 20 为目标，被 runner 强制跑在 Node.js 24 上。
**不影响结果**（8 个任务全绿）。要消掉它就把这两个 action 升到新主版本，
但升级本身有破坏风险，属于"有空再做的整洁性工作"，不着急。
