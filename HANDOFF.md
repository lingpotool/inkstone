# HANDOFF —— 交接文档

> 写给下一个接手的对话。读完这份 + `AGENT.md` + `ROADMAP.md`，就能直接开工。
> 交接时间：2026-09-10 · **567 个测试全绿** · 覆盖率 89% · 中文已渲染真字形

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
77fed3a ci: 三平台 CI + 覆盖率门禁 + 架构约束任务   ← HEAD
```

### ROADMAP Phase 1 进度

- ① 平台抽象 ✅ · ② 渲染管线 **部分**（缺 GL 后端 / 真字形）· ③ 文本 ✅
- ④ 布局 ✅ · ⑤ 组件树 ✅ · ⑥ 样式 ✅ · ⑦ 组件 ✅ · ⑧ 测试与 CI ✅

**Phase 1 剩下的是 DoD 而非交付项**：

- [x] 三平台 CI 全绿 —— **首次推送即 8 个任务全绿**（`77fed3a`）。
      黄金图在 Windows / macOS / Ubuntu 上**逐字节相同**，
      "确定性"从设计意图变成了可验证的事实。
- [ ] 中文输入：可输入、可删除、光标位置正确 ← **最大的一块，见下**
- [ ] 100%/125%/150% 缩放无模糊无错位
- [x] 布局引擎 100% 无窗口可测，覆盖 ≥85%（实测 89%）
- [ ] 样板 App（一个真实小工具）三平台可用

---

## 三、下一步（按 ROADMAP 顺序，别跳）

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
./.venv/Scripts/python.exe -m pytest tests -q          # 567 个必须全绿
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
2. 跑一遍验证命令确认起点全绿（567 passed / mypy 干净 / 覆盖 89%）
3. 跑一次 `python examples/hello.py` —— 亲眼看看现在的界面长什么样，
   这是最快建立"这个库到什么程度了"直觉的方式
4. 若继续 Phase 1，顺序建议：**①中文输入**（`events/` + 编辑模型）→
   **②DPI 缩放** → **③macOS/Linux 字体引擎** → **④样板 App**
5. 每完成一块：全量检查全绿 → commit（中文说明为什么）→ push

地基是结实的：三棵树、令牌、文本栈、确定性渲染、无头测试链路、
CI 全通了。三平台 CI 首次推送就全绿，黄金图逐字节一致，
中文已经渲染成真汉字。剩下的主要是"把交互接上"和"补齐另两个平台"。

---

## 九、唯一的上游噪音

CI 有一个非阻塞告警：`actions/checkout@v4` 与 `actions/setup-python@v5`
仍以 Node.js 20 为目标，被 runner 强制跑在 Node.js 24 上。
**不影响结果**（8 个任务全绿）。要消掉它就把这两个 action 升到新主版本，
但升级本身有破坏风险，属于"有空再做的整洁性工作"，不着急。
