# HANDOFF —— 交接文档

> 写给下一个接手的对话。读完这份 + `AGENT.md` + `ROADMAP.md`，就能直接开工。
> 交接时间：**2026-09-13** · 地基整改 R1–R6 + 收官包 R7 **全部完成** ·
> 无头单测 **944 个全绿**（覆盖率 89.8%）· 黄金图 12 张像素级比对
>
> **当前主线**：**GL 后端子包（`docs/22`）**——R7.5 的性能基准触发了预先写死的
> 决策规则（三场景 p95 超 10ms 预算 40–300 倍）。GL 包的范围与验收见 docs/22，
> **不在**已完成的 R1–R7 范围内，是下一件要动的事。
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
- [ ] 中文输入：可输入、可删除、光标位置正确 ← **最大剩余缺口**。
      R7.1 已接好"点击聚焦 + IME 通道打开 + 候选框位置上报"，
      但**编辑模型（文本/选区/组合态）还不存在**，属 Phase 2 首项。
- [x] 100%/125%/150% 缩放（R7.3）：逻辑像素布局、物理=逻辑×scale、
      文字在物理分辨率重光栅化；150% 黄金图已肉眼核对。125% 只有尺寸断言，
      未单独建黄金图（要更全可补 125%/200% 基线）。
- [x] 布局引擎 100% 无窗口可测，覆盖 ≥85%（实测 89.8%）
- [ ] 样板 App 三平台可用：R7.4 交付 `examples/notes.py`，headless 三平台出图；
      **SDL2 真窗口交互尚未在三平台各验一遍**
- [ ] 帧时间 p95 < 8ms：软件光栅实测差两个数量级，由 docs/22 的 GL 后端达成

---

## 三、下一步（按 ROADMAP 顺序，别跳）

### ① GL 后端子包（`docs/22`）—— 当前主线

R7.5 的规则先写死（p95 > 10ms），实测三场景 p95 = 859 / 3125 / 576ms，
热点是软件光栅的纯 Python 逐像素 SDF 与"整份显示列表全量光栅"。
docs/22 已定范围：**只实现现有显示列表 IR 的指令集**、沿用 R3.1 帧生命周期、
**不改 core**、不引第二套文本栈（字形仍来自 `HbFtFontEngine`）、
黄金图仍以软件光栅为事实源、不做 Skia。验收：三场景 p95 ≤ 10ms（参考机）。

### ② 文本编辑模型（Phase 2 首项）

`Input` 只能聚焦。要补：编辑模型 `text + selection(anchor, focus) + composition`
（docs/04 §6）、光标移动、退格/删除、选区替换。地基已就绪、直接用：

- `Paragraph.position_for_point(x, y)` → 点击落在哪个字符间隙
- `Paragraph.rects_for_range(start, end)` → 光标竖线与选区矩形
- `TextRunOp.underline` → IME 组合态下划线（已实现，尚未被使用）
- `backend/base.py` 的 `ImeEvent`（COMPOSE / COMMIT / CANCEL）+ `events/ime.py` 的 `ImeSession`

**注意**：ADR-0005 说整形与断行不自己造，但**编辑逻辑要自己写**——那是 UI 库的本职。

### ③ macOS / Linux 真机字体验证

R4 已用**同一套 HB+FT**取代三平台原生引擎，所以不是"再写两份引擎"，而是：
在 macOS / Linux 真机上验证系统字体目录扫描、回退链命中、内嵌兜底字体接管
（零系统字体时中文不出豆腐块）。接口（`MetricsProvider` + `GlyphProvider`）固定。

### ④ 三平台 SDL2 交互验证

R7.1–R7.3 的事件/手势/DPI 链路在 headless 下都有确定性测试，
但 `--sdl2` 真窗口路径需要在 Windows / macOS / Linux 各跑一遍
（点击、滚动、切主题、拖到不同 DPI 的屏幕）。

---

## 四、验证命令

```bash
cd /e/inkstone
./.venv/Scripts/python.exe -m pytest tests -q          # 944 个必须全绿
./.venv/Scripts/python.exe -m ruff check src tests examples benchmarks
./.venv/Scripts/python.exe -m ruff format --check src tests examples benchmarks
./.venv/Scripts/python.exe -m mypy                     # strict，零错误
./.venv/Scripts/python.exe -m pytest tests -q -m "not slow" \
    --cov=src/inkstone --cov-fail-under=85             # 覆盖率（89.8%）
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
2. 跑一遍验证命令确认起点全绿（944 passed / mypy 干净 / 覆盖 89.8%）
3. 跑一次 `python examples/notes.py` —— 看当前最完整的界面长什么样
4. 按第三节顺序推进：**①GL 后端子包（docs/22）** → ②文本编辑 → ③macOS/Linux
   真机字体验证 → ④三平台 SDL2 验证
5. 每完成一块：全量检查全绿 → commit（中文说明为什么）→ push

地基是结实的：三棵树、令牌、跨平台文本栈、确定性渲染、事件/手势/DPI 闭环、
无头测试链路、CI 全通。界面已经**能点、能滚、能切主题、能缩放**；
剩下的是"把字真正打进去"（编辑模型）、"把帧率做上去"（GL）、
以及"在真机上验一遍"。

---

## 九、唯一的上游噪音

CI 有一个非阻塞告警：`actions/checkout@v4` 与 `actions/setup-python@v5`
仍以 Node.js 20 为目标，被 runner 强制跑在 Node.js 24 上。
**不影响结果**（8 个任务全绿）。要消掉它就把这两个 action 升到新主版本，
但升级本身有破坏风险，属于"有空再做的整洁性工作"，不着急。
