# 贡献指南

## 0. 三条不可协商的规矩

1. **任何提交都不能让三平台 CI 变红。** 只在自己电脑上跑通不算跑通。
2. **新增能力必须带测试。** 布局类能力必须能在无窗口环境下测试（这是硬门槛，不是建议）。
3. **公开 API 变更必须更新 CHANGELOG 与文档。** 没有文档的能力等于不存在的能力。

## 1. 环境

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install
```

平台相关依赖（按需）：

```bash
pip install -e ".[sdl2]"     # 窗口与输入（Windows / macOS / Linux）
pip install -e ".[skia]"     # 高质量光栅后端
```

## 2. 常用命令

```bash
make check     # lint + 类型 + 测试，提交前必跑
make test      # 只跑单元测试（无需窗口）
make golden    # 更新截图基线
```

## 3. 分支与提交

- `main` 永远可发布；开发在 `feat/<主题>` / `fix/<主题>` 分支进行。
- 提交信息用 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)：
  `feat(layout): 支持 grid 的 fr 轨道` / `fix(ime): 候选框位置跟随光标`
- 一个 PR 只做一件事。超过 500 行改动请先开 issue 讨论。

## 4. 代码规范

- 类型注解全覆盖，`mypy --strict` 必须干净。
- 数据与几何类型一律 `frozen dataclass`，禁止可变共享状态。
- 分层依赖只能向下：`widgets` 不能反向 import `core` 之上的东西；`core` 不能 import `backend`。
- 渲染与布局代码**禁止直接读取时间、随机数、环境变量**，一律通过注入。

## 5. 测试要求

| 改动类型 | 必须附带 |
|---|---|
| 布局算法 | 单元测试（约束 → 尺寸 → 位置），无需窗口 |
| 视觉变化 | 黄金图基线更新 + 说明变更原因 |
| 输入 / IME | 平台标记测试，至少 Windows 与 macOS 各一条 |
| 公开 API | 文档 + 示例 + 弃用策略（如为破坏性变更） |

## 6. 评审关注点

评审者会按顺序问四个问题：

1. 它在三平台上都成立吗？
2. 它是确定性的吗（同样输入 → 同样输出）？
3. 它可以在没有窗口的情况下被测试吗？
4. 中文场景（输入法、字体回退、标点换行）被考虑了吗？
