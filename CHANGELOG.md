# 更新日志

本文件格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [未发布]

### 新增
- 项目骨架：`src/inkstone` 分层目录（平台后端 / 渲染 / 文本 / 布局 / 组件树 / 事件 / 无障碍 / 样式 / 组件 / 声明式协议 / 开发者工具）
- **行为原语层 `primitives/`**（8 模块骨架：focus_trap / roving_focus / popper / dismissible / portal / presence / scroll_lock / overlay_manager）——弹层与菜单的专业地基
- **动效引擎 `motion/`**（5 模块骨架：easing / spring / animation / transition / reduced_motion）
- **图标系统 `icons/`**（2 模块骨架：registry / stroke，Lucide 网格规范）
- 工程配置：`pyproject.toml`（hatchling + ruff + mypy strict + pytest）、`Makefile`、`.gitignore`、`.gitattributes`、`LICENSE`(MIT)
- 布局基础类型 `inkstone.layout.types`：`Offset` / `Size` / `Rect` / `EdgeInsets` / `BoxConstraints`（不可变、纯数据、无后端依赖）
- 上述类型的单元测试，验证"无窗口也能测布局"这条原则
- 设计文档：`00-愿景与定位`、`01-架构总览`、`12-架构审查与缺口分析`、`13-设计系统`
- `prototype/`：Phase 0 探针（约 1100 行可运行原型，已验证自绘、中文渲染、简化 flex、无头截图）

### 说明
- 当前版本 `0.1.0.dev0`，公开 API 尚未稳定，任何接口都可能变更。
- `prototype/` 不参与打包与安装，仅作为风险验证记录保留。

## 未来版本的记录方式

- `0.x` 阶段：允许破坏性变更，但必须在 CHANGELOG 的 "### 变更" 小节写明迁移方式
- `1.0` 之后：破坏性变更必须先经过一个次版本的废弃警告期
