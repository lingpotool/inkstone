# 21 · R7 交互闭环与 Phase 1 收尾

> 前置：R1–R6 全部完成（888 测试全绿，地基整改收官，见 docs/14–20）。
> 定位：Phase 1 的最后一块——让界面**能点、能滚、能打字、能切主题**，
> 并用一个真实样板 App 验收"这个库可以拿去写工具了"。
>
> **给施工 agent 的第一句话**：先读 `docs/14 §3 执行协议`（环境、验证命令、
> 铁律、提交规范），再读本文件。不要碰其他分包的范围。

## 背景：R7 为什么是这个范围

R5 打通了事件**模型**（`backend/base.py` 的 `PointerEvent` / `KeyEvent` /
`TextEvent` / `ImeEvent`，含 window_id、真时间戳、滚轮精度、IME 双通道），
R6 打通了状态传播（Inherited / signals）。但 **事件到手之后没有下一步**：
`events/pointer.py`、`keyboard.py`、`focus.py`、`command.py` 全是空壳，
组件的 `set_component_state` 没有任何调用方——界面是"能画不能摸"的。

R7 把这条链路闭合：

```
backend 事件 → 命中测试 → 三阶段路由 → 手势竞技场 → 组件状态/回调
```

## R7.1 命中测试与指针事件路由

**现状（都是真的，动手前自己复核一遍）**：

- `backend/base.py:156` `PointerEvent`：坐标是**窗口内逻辑像素**，含
  `window_id` / `time_ms` / `clicks` / `wheel_dx·dy` / `pointer_type`；
  `PointerKind` 含 `ENTER` / `LEAVE`（R5.5 已进模型）。
- `layout/box.py`：`RenderBox` 有 `_offset`（父级 `place_child` 放置）、
  `size`、`bounds`（`Rect.from_offset_size`）、`children`——
  命中测试的全部结构前提已具备，**不许给 RenderBox 再加一套位置簿记**。
- `widgets/form.py`：Button/Input 有 `set_component_state(ComponentState)`，
  八态配方（R6.3）已就位，缺的只是"谁来调它"。

**设计**：

1. `RenderBox.hit_test(position, result)`：自顶向下、**子级逆序**（后画的在
  上层）。默认实现：点落在自身 bounds 内 → 把自身加入命中链并递归子级。
   滚动容器（`layout/scroll.py`）必须按可视区**裁剪**命中——被滚出视口的
   子级点不到（这条最容易漏，单列测试）。
2. `events/pointer.py` 实现 `PointerRouter`：事件 → 命中链 →
   **三阶段分发**（捕获 父→子 → 目标 → 冒泡 子→父，`stop_propagation`
   显式叫停，docs/06 §5）。`ENTER`/`LEAVE`/hover 由 MOVE 事件的命中链
   **差分**生成，不信任后端直接发的（SDL 的 enter/leave 是窗口级的）。
3. 组件接线：Button 由路由驱动 HOVER/ACTIVE/FOCUS 状态 + `on_tap` 回调；
   Input 获焦后通过 `backend.set_ime_rect`（R5.7 已进协议）上报光标位置。

**测试**：命中顺序（叠层后画的赢）；滚动裁剪后点不到视口外子级；
enter/leave 差分（含"快速划过"不丢 LEAVE）；stop_propagation 截断冒泡；
Button 点一次回调恰好一次；layout/paint 阶段回调里改状态抛 FrameError
（沿用 R1 的阶段守卫，不许开洞）。

## R7.2 手势竞技场

**规格**：docs/06 §5——tap / double-tap / long-press / drag / scroll
不是独立判断，是**竞争**。列表里的按钮：按下后 8px 内抬起是 tap，
超过则列表滚动获胜，按钮收到取消。**8px 这个阈值进令牌**（不许写字面量，
test_architecture.py 的数值扫描会拦）。

**设计**：

1. `GestureArena` + `GestureRecognizer` 基类（Tap / DoubleTap / LongPress /
   Drag / Scroll），识别器在竞技场里声明胜负，事件不直达组件。
2. 时钟用**事件自带的 `time_ms`**（R5.2），长按的超时推进走
   `BuildOwner.on_frame_scheduled` 排帧检查——**不读墙上时钟**
   （docs/06 §4 确定性承诺，测试靠注入时间戳驱动）。
3. 取消是一等公民：识别失败的一方收到 cancel，组件必须能把
   ACTIVE 状态退回去（配合 R6.3 的八态配方）。

**测试**（docs/06 §8 验收项）：滚动容器内嵌按钮，tap/scroll 竞争行为
符合规格；双击不错发两次单击；长按计时用注入时间戳驱动，不睡真觉。

## R7.3 DPI 缩放接线

**现状**：`DPI_CHANGED` 事件与 per-window DPI 已通（R5.10）；
`gfx/transform.py` 的仿射栈支持等比缩放（R3.4，线宽/圆角/字形度量
随等比缩放一起缩放）——**两头都通了，中间没接**。

**设计**：缩放因子从后端进 `BuildOwner.begin_frame` 的上下文 →
显示列表录制时在根上压一个缩放变换；文本度量按缩放档位取
（hbft 字形掩码缓存键要含缩放档位，否则高分屏文字发虚）。
**"逻辑像素 vs 物理像素"的换算规则写进 docs/02**（事件坐标已是逻辑像素，
保持一致）。

**测试**：100%/150% 下同一棵树布局尺寸按比例缩放；DPI 变化事件到达后
下一帧用新档位；黄金图加一张 150% 缩放的。

## R7.4 样板 App（验收器，不是演示品）

**目标**：一个真实小工具——侧栏（导航）+ 列表（滚动）+ 表单（输入）+
明暗主题切换（根主题或 `ThemeScope`）。SDL2 下可跑，headless 下出黄金图。

**纪律**（这条比 App 本身重要）：**App 代码不许绕过框架机制**——
不许直接摸 backend、不许读墙上时钟、不许在组件里写字面量颜色。
发现"绕不过去"的地方 = 框架缺口，记入 docs/20 §R6.4 记账清单，
不许在 App 层打补丁糊住。

**测试**：进 CI 冒烟（同 `examples/hello.py` 的方式）+ 黄金图。

## R7.5 性能基准 → GL 后端决策（数据驱动，不凭感觉）

R2.3 立过规矩：**优化按数据驱动**。GL 后端（ROADMAP Phase 1 ②）做不做、
做到什么程度，由这一步的数字决定：

1. 在 `benchmarks/` 跑三个真实场景：长列表滚动、全屏重绘、文本密集页；
   出帧耗时分布（p50/p95），不是平均值。
2. 决策规则（先写死再量，不许量完再调阈值）：p95 > 10ms（60fps 预算的
   六成）→ 启动 GL 后端子包（**另立分包文档**，不在本包 scope）；
   否则记入 docs/20 §R6.4 后补清单，GL 让位于 Phase 2 铺组件。
3. 若做 GL：只实现显示列表 IR 的**现有指令集**（矩形/圆角/描边/裁剪/
   文本/路径），不发明新原语；协议不动 core（R3.1 的帧生命周期就是为
   它准备的）。

## R7 整包验收

- docs/06 §8 勾掉："手势竞技场：滚动容器内嵌按钮，tap/scroll 竞争符合规格"；
- 命中/路由/竞技场/焦点各有"修复前必红"的回归测试；
- 样板 App 在 SDL2 下真实可交互，headless 黄金图进 CI；
- benchmarks 出报告，GL 决策有据可查（无论结论是做还是缓）；
- `make check` 全绿（ruff + mypy strict 三平台 + pytest 含 slow）；
- docs/06 §5、docs/02 的 DPI 口径与实现一致。

**明确不做**（写在这里防 scope 蔓延）：GL/Skia 后端本体、macOS/Linux
字体之外的 backend 扩展、动效引擎接线（motion/ 仍骨架）、多窗口管理 UI。
