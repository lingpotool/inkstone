# HANDOFF —— 交接文档

> 写给下一个接手的对话。读完这份 + `AGENT.md` + `ROADMAP.md`，就能直接开工。
> 交接时间：2026-09-12 · 地基整改 R1–R6 全部完成 · 无头单测 **888 个全绿**
> （更早的更新记录按时间顺序在下面，越往下越旧）
>
> **2026-09-12 重要更新**：地基审查完成，结论与施工方案在 `docs/14-地基整改总览.md`
> （工作包 R1–R6，分包文档 docs/15–20）。**整改期间，新功能开发让位于地基整改**；
> 本文中"已完成"表格的部分条目实际状态比记录的弱，以 docs/14 第 1 节的审查结论为准。
>
> **2026-09-12 晚：R1「正确性止血」已完工（docs/15 的 11 个条目全部落地）。**
> 修掉 11 处静默断链与崩溃级 bug，每条都带"修复前必红"的回归测试；
> 无头单测 **567 → 610** 全绿。
> 黄金图基线 `text_wrapping.png` 因 R1.1 更新过一次（diff 已逐像素核对：
> 只有竖长画布底部 20 行由透明黑变成主题底色）。
>
> **2026-09-12 深夜：R2「测试求真」也已完工（docs/16 的 6 个条目全部落地）。**
> 无头单测 **610 → 662** 全绿。这一包改的是**门禁本身**：
>
> - 黄金图比对单位从 PNG 文件字节改成**解码后的 RGBA 像素**
>   （`encode_png` 的 zlib 输出不跨版本保证一致，"三平台逐字节相同"是侥幸）；
>   解码器在 `tests/png_compare.py`，5 种滤波器都实现，其余形态响亮报错。
> - **基线缺失 = 失败**（此前缺失就顺手写一份新基线然后绿灯——自动橡皮图章）。
> - 性能基准拆成 `test_layout_perf_incremental`（预算 5ms）与
>   `test_layout_perf_full`（预算 30ms）。原来的"全量 0.89ms"测的是**缓存命中**：
>   它每轮只调 `root.mark_needs_layout()`，而该方法只向上冒泡。
>   真全量实测 17.6ms，是那个数字的 20 倍。
> - "组件零硬编码"扫描从颜色扩到**圆角 / 描边宽度 / 控件高度三类数值**，
>   清掉 4 处漏网（Card 的 `or 10.0`、`stroke(rect, 1.0, ...)`、
>   `_ControlRenderObject` 的 36.0/1.0/10.0/12.0）；`elevation` 死字段删除。
> - conftest 删掉两个没人用的 fixture（`clock` / `any_backend`）。
>
> **2026-09-12 后半夜：R3「渲染协议重塑」也已完工（docs/17 的 6 个条目）。**
> 无头单测 **662 → 746** 全绿，覆盖率 89.5%。这一包趁消费者少（只有 devtools
> 与测试）把渲染层的**接口形状**改对，免得等 GL/Skia 与更多组件上来再改：
>
> - `RasterBackend` 从 `rasterize() -> FrameBuffer` 改成**帧生命周期**
>   `begin_frame(size, scale) → execute(display_list, clip) → end_frame() → screenshot()`。
>   旧形状把"读回内存"当成后端的唯一出口，GL/Skia 在结构上无法实现它——
>   协议画错了，不是实现偷懒。顺序用错抛 `RasterError`。
> - 帧缓冲归后端所有、**脏矩形是 `execute` 的参数**（显示列表保持"纯图纸"，
>   黄金图的比对语义不被重绘策略污染）。
> - `PositionedGlyph` 补 `y_offset`（HarfBuzz 四元组），**R4 落地的前提**。
> - 录制器的"累计平移"升级为 **2×3 仿射矩阵**（`gfx/transform.py`）：
>   DPI 缩放与 `motion/` 的缩放动画从此有地方可挂。等比缩放下线宽/圆角/
>   字形度量一起缩放；非等比下明确不缩放（圆角会变成椭圆角，当前指令表达不了）。
> - 新增 **path 指令形状**（扁平 verb 数组，拒绝 SVG 字符串），软件光栅
>   暂未实现 → **响亮抛 `NotImplementedError`**；它是 `icons/stroke.py` 的载体。
> - 软件光栅**不透明对齐矩形的整行切片快路径**：1280×800 全屏填充
>   **749ms → 3ms**（预算 50ms）。快路径与通用路径**逐比特相同**，
>   靠 `SoftwareRasterizer(fast_paths=False)` 的等价性测试钉住。
>
> **R3 里有一处我按证据偏离了文档的写法，需要你知道**（细节见提交信息）：
> docs/17 §R3.2 要求把 `_blit_mask` 的 `round()` 直接改成亚像素落位。
> 实现后实测 text_block 黄金图 5.6% 的像素变化、其中 2799 个"变亮"——
> 笔画被摊到邻像素、文字整体变软。原因是**内置字形是位图掩码**，本来就按
> 像素栅格生成，用小数相位重采样只会把它摊薄；而文档预期的"1px 抖动"不成立。
> 所以我把亚像素落位做成了 `SoftwareRasterizer(subpixel_glyphs=True)`，
> **默认关**，并留了测试。R4 换上 FreeType 的**轮廓**掩码后（掩码按相位生成、
> 不会变软）再打开才是收益。**没有更新任何黄金图基线**。
>
> **下一个该动的是 R4「文本栈替换」（docs/18）**：HarfBuzz + FreeType 自有文本栈
> （spike 已验证，参考实现是仓库根目录的 `_spike_hbft.py`）、内嵌 OFL 兜底字体、
> 真字体黄金图、删掉 GDI。R4 依赖 R2（测试求真）与 R3（`y_offset`），两者都已就绪。
>
> **2026-09-12 收官：地基整改 R1–R6 全部完成，无头单测 888 个全绿。**
> R4（docs/18）：文本栈换成 HarfBuzz 整形 + FreeType 光栅化 + 内嵌
> Inkstone Sans 兜底字体（零系统字体时中文也不出豆腐块），GDI 路线删除。
> R5（docs/19）：事件模型补 window_id/真时间戳/滚轮精度、IME 双通道
> （组合态与上屏分离）、呈现契约进协议、GLFW 空壳删除。
> R6（docs/20）：环境传播（`InheritedWidget` / `ThemeScope` 子树覆盖 +
> 定向标脏）与 signals（`Signal` / `Computed` / `Effect`，与 Inherited
> 共用"读时登记、写时标脏"内核）落地；变体状态配方逐变体补齐，
> 全变体×全状态过 WCAG AA 断言（顺手修掉暗色 `primary_press` 3.6:1 的
> 违规令牌）；`resolve` 引入 `UNSET` 哨兵。
>
> **R7「交互闭环与 Phase 1 收尾」（docs/21）进行中：R7.1–R7.3 已完成**。
> R7.1 命中测试与指针事件路由；R7.2 手势竞技场（`GestureArena` +
> Tap/DoubleTap/LongPress/Drag 竞争裁决、8px/500ms/300ms 进令牌、
> 输家收 cancel）；R7.3 DPI 缩放接线（档位经 `begin_frame(dpi_scale=…)`
> 进上下文，`flush_paint` 在根上压缩放 → 显示列表是设备像素、布局恒为
> 逻辑像素，`DPI_CHANGED` 下一帧生效，150% 黄金图）。
> 无头单测 **888 → 933** 全绿，覆盖率 89.8%。
>
> **下一个该动的是 R7.4「样板 App」**（docs/21）：侧栏导航 + 可滚动列表 +
> 表单输入 + 明暗主题切换的真实小工具；SDL2 下可跑、headless 出黄金图、
> 进 CI 冒烟。纪律：App 不许绕过框架机制（不直接摸 backend、不读墙上时钟、
> 不写死颜色），绕不过去的地方记入 docs/20 §R6.4 后补清单而不是打补丁。
> 之后是 R7.5 性能基准 → GL 后端决策。
>

---

## 一、这是什么项目

**inkstone** —— 专业的跨平台 Python 原生 UI 系统。纯 Python、自绘渲染、
Windows / macOS / Linux 一等公民、中文一等公民。

- 仓库：`https://github.com/lingpotool/inkstone`（`main` 分支，直接推）
- 14 篇设计文档在 `docs/`（编号 00–13），**是规格书不是装饰**，动手前先读对应那篇
- `AGENT.md` 是规则书（**含 ADR 表，务必先看**），`ROADMAP.md` 是推进顺序

**用户的合作偏好**：

- "你只要保证架构正确，专业就行" —— 授权全权决策，但**架构必须站得住**
- "按照整个项目的推进顺序，别搞乱了" —— **严格按 ROADMAP 推进**，别被单点带偏
- 用户会真的看渲染图，并给观感反馈（锯齿、圆角缺口都是他先发现的）

---

## 二、当前状态

### 已完成

| 模块 | 内容 |
|---|---|
| `layout/` | 协议/盒子/Flex/Grid/Stack/Scroll，164 测试，覆盖 92% |
| `core/` | 三棵树 + 帧调度 + `text_engine` 环境服务 |
| `backend/` | 归一化事件 + 无头后端 + SDL2 + **字体度量契约** + **Windows 真字体引擎（GDI）** |
| `style/` | 三层令牌 + 明暗主题 + 变体解析 |
| `text/` | **完整文本栈**：度量 / CJK 回退链 / 整形 / 断行（含禁则）/ 段落排版 |
| `widgets/` | Box / Card / **Text** / Row / Column / Flexible / Button / Input |
| `gfx/` | 显示列表（含 `TextRunOp`）+ 录制器 + 软件光栅（含文本）+ PNG |
| `devtools/` | 确定性截图 + 7 张黄金图 + **字形源自动配对** |
| `examples/` | `hello.py` 可运行（`--dark` / `--deterministic`），有冒烟测试 |
| CI | 三平台 + 覆盖率 + 性能 + 架构，五个任务 |

### 本次会话的 4 个提交（时间序）

```
2c9ed8c feat(text): 文本栈落地，度量下沉后端保证同源
d3baffd feat(text,widgets,gfx): 文本接入渲染管线，Text 组件落地
5392708 feat(widgets): Button 按标签收缩、Input 显示值/占位符
77fed3a ci: 三平台 CI + 覆盖率门禁 + 架构约束任务
（此后还有 4 个提交：真字体引擎、CI 两处修正、R1 之后的整改；见 `git log`）
```

### ROADMAP Phase 1 进度

- ① 平台抽象 ✅ · ② 渲染管线 **部分**（缺 GL 后端 / 真字形）· ③ 文本 ✅
- ④ 布局 ✅ · ⑤ 组件树 ✅ · ⑥ 样式 ✅ · ⑦ 组件 ✅ · ⑧ 测试与 CI ✅

**Phase 1 剩下的是 DoD 而非交付项**：

- [x] 三平台 CI 全绿 —— **首次推送即 8 个任务全绿**（`77fed3a`）。
      黄金图在 Windows / macOS / Ubuntu 上**像素级一致**（R2 之后比对单位是
      解码后的 RGBA，见 AGENT.md 的 CI 段），
      "确定性"从设计意图变成了可验证的事实。
- [ ] 中文输入：可输入、可删除、光标位置正确 ← **最大的一块，见下**
- [ ] 100%/125%/150% 缩放无模糊无错位
- [x] 布局引擎 100% 无窗口可测，覆盖 ≥85%（实测 89%）
- [ ] 样板 App（一个真实小工具）三平台可用

---

## 三、下一步（按 ROADMAP 顺序，别跳）

> **⚠️ 先看这里**：地基整改 R1–R6（docs/14–20）**已全部完成**（888 测试全绿）。
> R7「交互闭环与 Phase 1 收尾」（docs/21）进行中：**R7.1–R7.3 已完成**
> （933 测试全绿），当前该动的是 **R7.4 样板 App**。docs/21 是下面这几节
> （中文输入 / DPI / 样板 App）的正式施工版，以它为准。

### ① 中文输入与文本编辑 —— Phase 1 最大的剩余缺口

`widgets/form.py` 的 `Input` 现在只**显示**值/占位符，不能编辑。
要做完"可输入、可删除、光标位置正确"，需要：

1. **`events/` 事件系统**（当前是占位桩，L1）。要做的：
   命中测试 → 路由到目标元素 → 焦点管理。`docs/06` 有规格。
2. **编辑模型**：`text + selection(anchor, focus) + composition(range)`，
   见 `docs/04 §6`。光标移动、退格/删除、选区替换。
3. **接上现成能力**（这些已经做好了，直接用）：
   - `Paragraph.position_for_point(x, y)` → 点击落在哪个字符间隙（文本编辑的地基）
   - `Paragraph.rects_for_range(start, end)` → 光标竖线与选区矩形（光标是零宽矩形）
   - `TextRunOp.underline` → IME 组合态下划线（已实现，尚未被使用）
   - `backend/base.py` 的 `ImeEvent`（COMPOSE / COMMIT / CANCEL）
4. **IME 组合态**完整渲染属 Phase 2（ROADMAP Phase 2 item 1），
   Phase 1 只要求"可输入、可删除、光标位置正确"。

**注意**：`docs/04 §2` 的 ADR-0005 说整形与断行不自己造，但**编辑逻辑要自己写**——
那是 UI 库的本职。

### ② DPI 缩放（100%/125%/150%）

无头后端的 `set_dpi_scale()` 早就为这件事准备好了，还没有人用它。
关键约束：**布局永远在逻辑像素里算**，物理像素 = 逻辑 × dpi_scale，
换算只在光栅/呈现那一层做。文字要在物理分辨率上光栅化才不模糊。

### ③ 真字形的另外两个平台

Windows 已经好了（中文是真汉字）。macOS 走 CoreText、Linux 走 FreeType
（或 fontconfig + 一个纯 Python 的 TTF 光栅器）。接口是现成的：
实现 `MetricsProvider` + `GlyphProvider`，照 `backend/gdi_fonts.py` 抄结构。
**注意别只实现字形不实现度量**——那会让字距与字形对不上。

### ④ 样板 App

Phase 1 的成功标准是"能用它写出一个真实的小工具"。
`examples/` 只有 1 个文件。建议做一个小而完整的东西（如本地笔记 / 剪贴板历史），
它会逼出所有还缺的能力。

---

## 四、验证命令

```bash
cd /e/inkstone
./.venv/Scripts/python.exe -m pytest tests -q          # 662 个必须全绿
./.venv/Scripts/python.exe -m ruff format --check src tests
./.venv/Scripts/python.exe -m ruff check src tests     # 必须 All checks passed
./.venv/Scripts/python.exe -m mypy                     # strict，必须零错误
./.venv/Scripts/python.exe -m pytest tests -q -m "not slow" \
    --cov=src/inkstone --cov-fail-under=85             # 覆盖率（89%）
```

更新黄金图：`INKSTONE_UPDATE_GOLDEN=1 pytest tests/unit/test_golden_form.py tests/unit/test_text_render.py`
→ **更新后必须肉眼打开 PNG 确认**，再正常模式复跑确认绿。

**性能测试必须无插桩单独跑**（`-m "slow"`）：覆盖率插桩会让它们随机变红。

---

## 五、已知问题（诚实清单）

1. **真字形目前只有 Windows 一份**（`GdiFontEngine`）。中文已经渲染成**真正的
   汉字**，不再是方块。macOS（CoreText）与 Linux（FreeType）还需各写一份，
   接口（`MetricsProvider` + `GlyphProvider`）已固定，照 `gdi_fonts.py`
   的结构写即可，上层零改动。
2. **彩色 emoji 会退化成单色轮廓**：GDI 的灰度抗锯齿路径画不了 COLR/CBDT
   彩色字体。宽度与位置是对的（排版不受影响），但要真彩色需要
   Direct2D/DirectWrite。
3. **字号是整数像素**：GDI 的 `lfHeight` 只有整数，字号带小数时会被取整。
   要和 ↓ 的 DPI 缩放一起做才精确。
4. **`_fill` / `_stroke` 的裁剪边界有 1px 越界**：`_column_range` / `_row_range`
   用 `int(right) + 1` 当上界，裁剪边恰好落在整数像素上时会多放一列/一行。
   文本路径已用 `ceil` 修正；形状路径未改（会波及既有黄金图，需单独提交）。

5. **架构上有 4 处已登记的反向依赖**（`test_architecture.py` 的 `KNOWN_EXCEPTIONS`）：
   gfx/text → layout（几何原语属共享内核）、backend → gfx、core → style。
   登记表不允许留失效条目，消除了就要删掉。

6. Flex 还不支持 `wrap` 换行（`docs/05 §4`）。滚动条、锚点保持属 Phase 2。

7. `.gitattributes` 声明 `eol=lf` 但工作区多为 CRLF，三平台 CI 会产生幽灵 diff。
   修法：`git add --renormalize .`（大 diff，单独提交）。

8. **待用户拍板**：`docs/13 §8` 写"… → 状态 → 实例覆盖"，
   `style/resolve.py` 把**交互态放在最后**（否则实例 `bg=red` 会悄悄关掉 hover）。
   改回来只需对调两行。`AGENT.md` 待办里也记了。

---

## 六、这一程积累的硬规矩（AGENT.md 有完整版）

最容易被踩的几条：

1. **文字宽度只度量，不估算。** 一切经 `Backend.measure_text`。
   组件里写"每字 N 像素"= 审查打回。
2. **按字素簇操作，不按码点。** 断行/选区/省略号/脚本分段都要先过
   `grapheme_clusters()`，否则会把 emoji 劈成半张脸。
3. **渲染层按 `glyph.x` 画，不自己累加 advance。**
4. **字形必须画进 advance 的宽度里**，否则相邻字重叠。
5. **`RenderObject` 拿不到 `BuildOwner`**，环境服务一律"元素写、渲染对象读"；
   且**挂载时只调 `create_render_object`**，字段必须在 create 里填满
   （踩过两次：Text 与 Button 都因此"首次布局量到空值，且不报错"）。
6. **渲染问题必须打像素看。** 把像素转 ASCII 打到终端（`.`=底 `#`=实
   `+`=半透明），比任何断言都直观。
7. **先量化确认问题存在，再动手改。**
8. 提交信息用中文，说清"为什么"，格式参考 `git log`。

---

## 七、环境速查

- Python venv：`E:\inkstone\.venv`（Python 3.13，Windows）
- 命令一律从 `cd /e/inkstone` 开始，用 `./.venv/Scripts/python.exe`
- 依赖：**零运行时依赖**（PNG 编码都是 stdlib 手写的）
- 临时产物放 `.private/`（已 gitignore）；黄金图失败产物在
  `tests/golden/failures/`（CI 失败时会上传成 artifact）
- 原型 `prototype/` 已冻结，不参与打包

---

## 八、开工姿势（建议）

1. 读 `AGENT.md`（**重点看 ADR 表**）→ `ROADMAP.md` → 本文档
2. 跑一遍验证命令确认起点全绿（662 passed / mypy 干净 / 覆盖 89%）
3. 跑一次 `python examples/hello.py` —— 亲眼看看现在的界面长什么样，
   这是最快建立"这个库到什么程度了"直觉的方式
4. 若继续 Phase 1，顺序建议：**①中文输入**（`events/` + 编辑模型）→
   **②DPI 缩放** → **③macOS/Linux 字体引擎** → **④样板 App**
5. 每完成一块：全量检查全绿 → commit（中文说明为什么）→ push

地基是结实的：三棵树、令牌、文本栈、确定性渲染、无头测试链路、
CI 全通了。三平台 CI 首次推送就全绿，黄金图像素级一致，
中文已经渲染成真汉字。剩下的主要是"把交互接上"和"补齐另两个平台"。

---

## 九、唯一的上游噪音

CI 有一个非阻塞告警：`actions/checkout@v4` 与 `actions/setup-python@v5`
仍以 Node.js 20 为目标，被 runner 强制跑在 Node.js 24 上。
**不影响结果**（8 个任务全绿）。要消掉它就把这两个 action 升到新主版本，
但升级本身有破坏风险，属于"有空再做的整洁性工作"，不着急。
