"""目标 API 示例（Phase 1 完成后可真正运行）。

这个文件存在的意义是**先把 API 长什么样固定下来**，再让实现去追它。
如果你觉得这段写起来别扭，那就是 API 设计有问题——请提 issue，而不是迁就实现。
"""

from inkstone import __version__


def build():
    """预期写法（暂未实现，Phase 1 交付后可用）。"""
    # from inkstone import App, Button, Column, Row, Text, Input
    #
    # app = App(title="Hello", size=(480, 320))
    # app.mount(Column(padding=24, gap=12, children=[
    #     Text("你好，inkstone", size=22),
    #     Row(gap=8, children=[
    #         Input(placeholder="说点什么…", w="fill"),
    #         Button("发送"),
    #     ]),
    # ]))
    # return app
    raise NotImplementedError("引擎尚未实现，见 ROADMAP.md 的 Phase 1")


def main() -> None:
    print(f"inkstone {__version__}")
    print("当前阶段：架构设计 + 目录骨架已就绪，引擎实现尚未开始。")
    print("下一步：pip install -e '.[dev]' 然后 pytest")


if __name__ == "__main__":
    main()
