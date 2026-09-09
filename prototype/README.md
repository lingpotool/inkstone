# Phase 0 探针（已冻结，不属于产品代码）

> ⚠️ 本目录是**风险验证原型**，不是产品。它不参与打包、不在 `src/` 内、不保证维护。
> 它的历史使命已经完成：证明"自绘 + 中文渲染 + 简化布局 + 无头截图自检"四件事站得住。
> 真正的产品从 `../src/inkstone/` 开始，计划见 `../ROADMAP.md`。
> 运行方式：`cd prototype && pip install pyglet && python demo.py`

---

# inkstone —— 为「AI 来写」而生的 Python 原生 UI 系统

一套从零写的 Python 桌面 UI 系统。**没有 Tk、没有 Qt、没有 WebView、没有 CSS**，
唯一的底层依赖是一个能开窗口、能画像素的绘图库（pyglet，纯 pip 安装，约 2MB）。
窗口、布局、控件、命中测试、焦点、光标、文本排版——全部是这套代码自己实现的。

当前规模：**约 1100 行 Python，6 个模块**，附带一个能日常使用的示例应用。

## 为什么要有这套东西

AI 时代写工具，最大的摩擦不是"写代码"，而是：

1. 工具链太重 —— Node/Flutter 要装 SDK、配构建、等编译，AI 生成的代码还依赖你机器上的具体版本；
2. 旧 UI 库对 AI 不友好 —— Tk 的隐式布局、Qt 的信号槽样板，AI 容易写歪，且界面上限低；
3. AI 真正擅长的是**在一个受限的、声明式的面里填内容**，而不是从零发明架构。

inkstone 的答案：把"架构"锁死在这 6 个文件里，以后每个新工具，AI 只写"内容"那一层。

## 运行

```bash
pip install pyglet
python demo.py            # 灵感速记示例应用
python demo.py --shot x.png   # 无头截图（自动化验收用）
```

## 架构（6 个文件，依赖单向向下）

```
demo.py            你的应用：只描述"有什么"，不关心怎么画
   ↓
spec.py            声明式构建：dict/JSON → 节点树（给 AI 用的一层）
   ↓
widgets.py         控件：Text / Button / Input / Card / Badge / NavItem / Sidebar …
   ↓
node.py            布局引擎：简化版 flex（行列 / gap / fill / align / justify / padding）
   ↓
gfx.py             渲染后端：把"画圆角矩形/写字"翻译成图元，按帧复用对象
   ↓
tokens.py          设计令牌：颜色、间距、圆角、字号——改这一个文件，全盘换肤
```

`app.py` 是外壳：窗口、事件路由、命中测试、焦点管理、每帧重算布局。

## 核心心智模型

- **一切皆节点**。`Node` 有外框 box 和内容框 cbox，布局就是往里填数字。
- **尺寸三态**：`w=200`（固定）、`w="fill"`（吃掉主轴剩余空间）、`None`（按内容）。
- **容器两轴**：`direction` 决定主轴，`gap` 是间距，`justify` 管主轴分配，`align` 管交叉轴。
- **颜色可以写语义名**：`bg="surface"`、`border=("border_strong", 1)`，运行时查主题表。
- **自绘即真相**：控件不向系统要控件，所以长什么样 100% 由令牌决定。

## 最小示例

```python
from inkstone import App, Column, Row, Text, Button, Input

app = App(title="计数器", size=(420, 200))
count = {"n": 0}
label = Text("0", size=28, text_align="center", w="fill")

def bump():
    count["n"] += 1
    label.value = str(count["n"])

app.mount(Column(padding=24, gap=12, children=[
    label,
    Row(gap=8, children=[
        Input(placeholder="备注…", w="fill"),
        Button("加一", on_click=bump),
    ]),
]))
app.run()
```

## 声明式（给 AI 用的那条路）

AI 写 JSON 比写嵌套构造函数更稳——结构错了一眼看出来，语法不会错：

```python
from inkstone import App, build

tree = build({
    "type": "column", "padding": 24, "gap": 12, "children": [
        {"type": "text", "value": "你好", "size": 22},
        {"type": "row", "gap": 8, "w": "fill", "children": [
            {"type": "input", "placeholder": "说点什么", "w": "fill"},
            {"type": "button", "label": "发送", "on_click": "send"},
        ]},
    ],
}, handlers={"send": lambda: print("go")})

app = App().mount(tree)
app.run()
```

## 已验证的能力

- 中文渲染（微软雅黑）与中文文本测量
- 悬停 / 按压态、手型与文本光标、焦点与光标闪烁
- 明暗两套主题（示例里有切换按钮）
- 无头截图（`--shot`），可直接作为自动化验收的"眼睛"

## 已知边界（诚实清单）

- **IME 候选框定位**：文字上屏走系统通路没问题，但输入法候选窗的位置跟随系统默认，
  还没有钉到光标旁边——这是所有自绘 UI 的共同深水区，排在路线图第一位。
- 没有滚动容器和裁剪（内容超出即溢出），列表很长时需要加分页。
- 没有快捷键系统、无障碍（屏幕阅读器）支持。
- 高分屏按系统 DPI 缩放，尚未做逐显示器精确缩放。

## 路线图

1. IME 组合窗定位（`ImmSetCandidateWindow`）
2. 滚动容器 + 裁剪
3. 虚拟化长列表
4. 把 spec 层升级成正式协议（schema 校验 + 热重载）
