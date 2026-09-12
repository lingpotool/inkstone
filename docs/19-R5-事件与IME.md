# 19 · R5 事件与 IME

> 前置：R3（呈现协议接缝）。本包修"输入"这条命脉——项目的立身之本
> （"中文 IME 深度控制"是 README 第一行卖点）目前是物理不通的。

## R5.1 事件模型加 `window_id`（破坏性，但越晚越贵）

**根因**：`backend/base.py:137-186` 的事件数据类没有 `window_id`；SDL 每个事件
结构体都带了 window id（sdl2.py:178 等），翻译时全部丢弃。多窗口是 docs/02 §2
的承诺，但事件到手后无法知道属于哪个窗口。

**修法**：所有事件类加 `window_id: int`；`HeadlessBackend` 与 `SDL2Backend` 填充；
所有构造点与消费方同步改。**现在只有 headless 测试在消费，改动几乎免费**——
这是"立即做"的全部理由。

**测试**：双窗口 headless 注入事件，断言各自只收到自己的事件。

## R5.2 事件时间戳改用事件自带值

**根因**：`sdl2.py:356` 用 `SDL_GetTicks()`（泵时刻）给所有事件打时间戳，
一帧内积压的 20 个事件拿到几乎相同的时间——双击判定、拖拽速度、手势超时
全建立在这个时间上。SDL 事件结构体里明明有 `timestamp` 字段（sdl2.py:177）却没读。

**修法**：翻译时读结构体的 `timestamp`（SDL 的毫秒 tick），保持"时钟由后端注入"
的纪律不变（headless 注入的时钟语义不变——headless 事件的时间戳由注入方指定）。

## R5.3 `wait_events` 丢事件（SDL2 正确性 bug）

**根因**：`sdl2.py:346-351`：`SDL_WaitEventTimeout` 把一个事件取进传入的 buffer，
该 buffer 随后被丢弃，翻译发生在 `pump_events()` 的新 buffer 上——
**每次唤醒稳定丢掉唤醒它的那个事件**（如用户按下的第一下键）。

**修法**：`wait_events` 拿到的事件必须进入本批翻译流程，不许丢。

## R5.4 滚轮：方向与精度

- 处理 `SDL_MOUSEWHEEL_FLIPPED`（macOS 自然滚动）——现状 macOS 滚动方向是反的；
- 结构体补 `preciseX/preciseY`（SDL 2.0.18+，触摸板平滑滚动的浮点增量）并优先使用；
- 滚轮事件的坐标用 SDL 提供的真实指针位置（sdl2.py:402-403 硬编码 0,0）。

## R5.5 指针模型补字段

`PointerEvent` 增加：`pointer_type`（mouse/touch/pen）、`pointer_id`、`clicks`
（双击信息，sdl2.py:207 的 `clicks` 读了却没进事件）、`pressure`（预留默认 1.0）。
发射 `PointerKind.ENTER/LEAVE`（SDL_WINDOWEVENT_ENTER=10/LEAVE=11 目前未处理）。
触摸板的 SDL_MULTIGESTURE（捏合）本包只留事件类型，手势识别属后续。

## R5.6 键码：scancode 与 keysym 分离

**根因**：`sdl2.py:360-363` 用 `SDL_GetKeyName(keysym)` 当 `code`——布局相关，
AZERTY 上快捷键会漂。W3C `code` 的语义是**物理键位**。

**修法**：`code` ← `SDL_GetScancodeName(scancode)`（物理位）；`key` ← keysym name
（布局相关，用于文本语义）；`"Keypad Enter"` 不再合并进 `"Enter"`（sdl2.py:118）。

## R5.7 IME 通道（本包核心）

**根因（三重断裂）**：

1. 全仓库从未调用 `SDL_StartTextInput`——SDL2 的 TEXTINPUT/TEXTEDITING 事件
   默认不产生，`sdl2.py:369-385` 的 IME 翻译代码在真机上**永远不会被执行**；
2. `Backend` 协议没有"设置候选框位置"方法（docs/02 §2 协议表里承诺了）；
3. TEXTINPUT 被笼统映射为 `ImeKind.COMMIT`——按普通字母键也算"IME 上屏"，
   概念混淆；`ImeKind.CANCEL` 定义了但全仓库无发射点。

**修法**：

1. `Backend` 协议增加：
   `start_text_input(window_id)` / `stop_text_input(window_id)` /
   `set_ime_rect(window_id, rect)`（候选框跟随光标）；
2. SDL2 后端实现三者（`SDL_StartTextInput` / `SDL_SetTextInputRect`）；
3. **拆两条通道**：text input（普通字符上屏）与 IME composition（组合态/上屏/取消）
   是两个事件类，不再混用 `ImeKind.COMMIT`；
4. `events/ime.py`（现为 8 行空壳）实现统一内部模型：
   `composing: bool` / `composition_text` / `composition_cursor`，对接 docs/04 §6 的
   输入框内部表示（`text + selection + composition`）；
5. `CANCEL` 在 SDL TEXTEDITING 取消路径发射。

**测试**：翻译层拆纯函数测（SDL 事件结构体 → 归一化事件）；协议一致性测试
（见 R5.9）；headless 实现这三个方法（记录调用，供上层测试断言）。

## R5.8 呈现契约进协议

**根因**：`present()` 只存在于 headless（headless.py:286），`Backend` 协议没有；
SDL2 后端没有任何 renderer/GL/swap 代码——"帧怎么上屏"这个后端最核心的职责
在协议里是空白，也是 `test_architecture.py` 里 `backend → gfx` 反向依赖登记的来源。

**修法**：与 R3.1 的 `RasterBackend` 对齐后，在 `Backend` 协议定义
`begin_frame/end_frame(present)` 接缝；SDL2 后端持有一个 `RasterBackend` 实例
（组合而非继承）；消掉 `backend → gfx` 的架构例外登记。

## R5.9 协议一致性必须可测

**根因**：`SDL2Backend` 不满足 `Backend` 协议（缺全部 `MetricsProvider` 方法），
而 base.py:217 自称"实现者：HeadlessBackend、SDL2Backend"——最危险的一类假保证。

**修法（已定）**：把 `MetricsProvider` 从 `Backend` 协议**拆出**——
度量引擎由 App 组装时注入（R4 的 HB+FT 引擎，三平台同一个），
窗口后端不再假装自己会量字。加显式的协议符合性测试
（遍历协议方法逐一断言实现存在且可调用），不依赖 `runtime_checkable` 的装饰器摆设。

## R5.10 SDL2 杂项正确性修复（同批做完）

- 窗口位置常量：`sdl2.py:314-315` 的 `-1` 应为 `SDL_WINDOWPOS_CENTERED = 0x2FFF0000`；
- `_SDL_WINDOWEVENT_CLOSE = 2` 是错的（2 是 HIDDEN，CLOSE 是 14），sdl2.py:55；
- 剪贴板 `SDL_GetClipboardText` 的返回值按 SDL 契约需要 `SDL_free`（sdl2.py:457-459），
  现状每次读剪贴板泄漏一次；
- 删除 sdl2.py:496 的死代码；
- DPI：处理 `SDL_WINDOWEVENT_MOVED`（跨屏拖动 → 轮询 display scale → 发射
  `DPI_CHANGED`）；SDL < 2.24 用 `SDL_GetDisplayDPI` 兜底；headless 的 DPI
  改为 per-window（`WindowSpec` 已有位置）。

## R5.11 GLFW 处置（已定：删除）

GLFW 没有 IME 组合 API，过不了项目自己的硬门槛（docs/02 §3）——
一个通不过硬门槛的"降级方案"不是降级方案。删除 `backend/glfw.py` 空壳，
docs/02 §3 与 AGENT.md 的对应表述改为"SDL2 唯一窗口后端，headless 用于测试"。

---

## R5 整包验收

- IME：headless 测试能断言 `start_text_input`/`set_ime_rect` 被正确调用；
  SDL2 后端的 IME 翻译函数有纯函数测试；`CANCEL` 有发射路径测试；
- 事件模型：window_id / 真时间戳 / 滚轮方向 / precise 增量各有测试；
- `wait_events` 丢事件的回归测试（构造注入事件序列）；
- 协议符合性测试红绿可控：故意删一个方法会红；
- `make check` 全绿；架构例外登记表减少（`backend → gfx` 消除后删条目）。
