"""组件树与状态（L5）：Widget / Element / RenderObject 三层结构。

一句话记住这套架构：

    Widget        图纸。不可变配置，廉价到可以每帧重建
    Element       工地。生命周期 + 状态持有，按"类型 + Key"复用
    RenderObject  楼。持有几何与脏标记，真正被布局与绘制

为什么这么分：Python 的对象分配不便宜，"全量重建 + diff"在大树上每帧会产生
数千次临时对象。让 Widget 廉价重建、Element 复用、RenderObject 增量更新，
是业界验证最充分的可变状态 UI 架构。

典型用法：

    ```python
    owner = BuildOwner()
    owner.mount(MyApp())
    owner.begin_frame(BoxConstraints(max_width=800, max_height=600))

    # 状态变更 → 下一帧生效
    some_state_holder.set_state(lambda: ...)
    owner.begin_frame(BoxConstraints(max_width=800, max_height=600))
    ```

状态：三棵树与帧调度已实现；signals（高频更新）未实现，见 docs/06 §3。
"""

from .binding import (
    MAX_BUILD_FAILURES_PER_FRAME,
    BuildError,
    BuildFailure,
    BuildOwner,
    FrameError,
    FramePhase,
)
from .element import (
    ComponentElement,
    Element,
    LeafRenderObjectElement,
    MultiChildRenderObjectElement,
    RenderObjectElement,
    StatefulElement,
    StatelessElement,
)
from .key import Key, ValueKey
from .render_object import PaintContext, RenderObject
from .widget import (
    RenderObjectWidget,
    State,
    StatefulWidget,
    StatelessWidget,
    Widget,
)

__all__ = [
    "MAX_BUILD_FAILURES_PER_FRAME",
    "BuildError",
    "BuildFailure",
    "BuildOwner",
    "ComponentElement",
    "Element",
    "FrameError",
    "FramePhase",
    "Key",
    "LeafRenderObjectElement",
    "MultiChildRenderObjectElement",
    "PaintContext",
    "RenderObject",
    "RenderObjectElement",
    "RenderObjectWidget",
    "State",
    "StatefulElement",
    "StatefulWidget",
    "StatelessElement",
    "StatelessWidget",
    "ValueKey",
    "Widget",
]
