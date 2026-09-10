# AGENT.md

给在这个仓库里干活的 AI / 新贡献者看的。读一遍再动手，能省掉大部分返工。

## 这是什么

**inkstone** —— 专业的跨平台 Python 原生 UI 系统。纯 Python 编写、自绘渲染、
Windows / macOS / Linux 一等公民、中文一等公民。

一句话目标：**让"用 Python 描述界面"成为一件专业、可靠、能发布到三个平台的事。**

## 当前状态（动手前先看这里）

Phase 1 · 地基进行中。真实代码只集中在 **布局引擎**：

| 模块 | 状态 |
|---|---|
| `layout/types.py` | ✅ 几何原语（不可变） |
| `layout/protocol.py` | ✅ 轴 / 对齐 / Sizing / LayoutError |
| `layout/box.py` | ✅ RenderBox 盒子模型 |
| `layout/flex.py` | ✅ Row / Column |
| `layout/grid.py` | ✅ Grid（fixed / fr / auto 轨道 + span） |
| `layout/stack.py` | ✅ Stack / Positioned / Align |
| `layout/scroll.py` | ✅ ScrollView（向子级派发无限主轴约束） |
| `core/`（key / widget / element / render_object / binding） | ✅ 三棵树 + 帧调度 |
| `backend/`（base / headless / sdl2） | ✅ 平台抽象层：归一化事件 + 无头后端 + SDL2 |
| `gfx/color.py` | ✅ Color（hex 解析、插值、WCAG 对比度） |
| `style/`（tokens / theme / resolve / variants） | ✅ 三层令牌 + 明暗主题 + 变体解析 |
| `widgets/`（basic / layout / form） | ✅ Box / Card / Row / Column / Flexible / Button / Input |
| `gfx/`（display_list / paint / raster.base / raster.software） | ✅ 显示列表 + 录制器 + 软件光栅 + PNG |
| `devtools/screenshot.py` | ✅ 确定性截图 + 黄金图基线（5 张） |
| 其余 58 个模块（gfx GL+Skia / text / events / primitives …） | ⬜ 占位桩 |

342 个无头单测全绿，**黄金图逐字节比对**也跑通。登录表单
（Card + 两个 Input + Row 里一个 ghost 取消 + 一个 fill 登录按钮）
能完整画成 PNG 并在每次跑测试时与基线逐字节相等。

按 ROADMAP 顺序，Phase 1 剩下：③ 文本（字体度量与 CJK 回退链）→
⑦ Text 组件 → ⑧ CI 配置。文本是 Text 组件与"中文输入"验收项的前置。

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

### 关于 ruff 的一条重要配置

`pyproject.toml` 里永久忽略了 **RUF001 / RUF002 / RUF003**。
这三条会把中文标点（，。（）「」）判成"易混淆的 Unicode 字符"——
对中文项目纯属误报。**不要"顺手"把它们打开**，改完会瞬间冒出 600+ 个假错误。

## 已确立的实现约定

这些是踩过坑之后定下来的，改动前先想清楚：

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
- 性能预算：1000 节点全量布局 < 5ms（测试里放宽到 2ms 留 CI 余量）。

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

## 已知待办

- `.gitattributes` 声明 `eol=lf`，但工作区多数 `.py` 实际是 CRLF。
  三平台 CI 上会产生幽灵 diff。修法：`git add --renormalize .`（会产生大 diff，单独提交）。
- **docs/13 §8 与实现有一处待裁决的分歧**：文档写"… → 状态 → 实例覆盖"，
  `style/resolve.py` 把**交互态放在最后**（否则实例 `bg=red` 会悄悄关掉 hover 反馈）。
  若按文档原文来，把 `resolve()` 里 state 与 overrides 位置对调即可。需要你拍板。
- Flex 还不支持 `wrap` 换行（docs/05 §4 有这条）。
- Scroll 只做了单/双向偏移。滚动条、锚点保持、过滚动按 ROADMAP 属于 Phase 2。
