"""灵感速记 —— inkstone 的示例应用。

跑起来：  python demo.py
截图：    python demo.py --shot preview.png
"""

import json
import os
import sys
import time

from inkstone import (
    App, Badge, Button, Card, Column, Input, NavItem, Row, Spacer, Text,
    F, R, S,
)

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ideas.json")
CATS = ["全部", "人物", "情节", "金句", "待展开"]

state = {"cat": 0, "items": []}


def load():
    if os.path.exists(DATA):
        try:
            with open(DATA, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return [
        {"text": "主角在第七年回到旧城，发现当年那家面馆还在，老板却认不出他了。",
         "cat": "情节", "ts": time.time() - 86400},
        {"text": "她说话总先笑一下，像给每句话垫一层软垫——其实她从没同意过任何人的提议。",
         "cat": "人物", "ts": time.time() - 3600},
        {"text": "人不是慢慢变老的，是在某个没睡好的凌晨，突然老了。",
         "cat": "金句", "ts": time.time() - 600},
    ]


def save():
    with open(DATA, "w", encoding="utf-8") as fh:
        json.dump(state["items"], fh, ensure_ascii=False, indent=2)


def stamp(ts):
    delta = time.time() - ts
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta / 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta / 3600)} 小时前"
    return f"{int(delta / 86400)} 天前"


app = App(title="灵感速记 · inkstone", size=(1040, 680))

entry = Input(placeholder="记下此刻的念头，回车保存…", w="fill", on_submit=None)
stat = Text("", size=F.sm, color="text_dim")
list_col = Column(gap=S.s3)
nav_items = []


def add_item(text):
    text = (text or "").strip()
    if not text:
        return
    cat = CATS[state["cat"]] if state["cat"] > 0 else "未归类"
    state["items"].insert(0, {"text": text, "cat": cat, "ts": time.time()})
    entry.value = ""
    save()
    rebuild()


def remove(idx):
    if 0 <= idx < len(state["items"]):
        state["items"].pop(idx)
        save()
        rebuild()


def pick_cat(idx):
    state["cat"] = idx
    rebuild()


def toggle_theme():
    app.set_theme("dark" if app.theme.name == "light" else "light")
    theme_btn.label = "浅色" if app.theme.name == "dark" else "深色"
    rebuild()


def visible():
    if state["cat"] == 0:
        return list(enumerate(state["items"]))
    name = CATS[state["cat"]]
    return [(i, it) for i, it in enumerate(state["items"]) if it["cat"] == name]


def rebuild():
    for i, item in enumerate(nav_items):
        item.active = (i == state["cat"])

    rows = visible()
    list_col.children = []
    if not rows:
        list_col.add(Card(w="fill", padding=S.s6, children=[
            Text("这个分类还是空的", size=F.md, color="text_dim",
                 w="fill", text_align="center"),
        ]))
    for idx, item in rows:
        list_col.add(Card(w="fill", children=[
            Row(w="fill", align="start", gap=S.s3, children=[
                Column(grow=True, gap=S.s2, children=[
                    Text(item["text"], size=F.md, w="fill", wrap=True),
                    Row(gap=S.s2, children=[
                        Badge(item["cat"], tone="primary" if item["cat"] != "未归类" else "muted"),
                        Text(stamp(item["ts"]), size=F.sm, color="text_faint"),
                    ]),
                ]),
                Button("删除", variant="ghost", on_click=(lambda i: (lambda: remove(i)))(idx)),
            ]),
        ]))

    total = len(state["items"])
    shown = len(rows)
    stat.value = f"共 {total} 条 · 当前显示 {shown} 条 · 数据在 ideas.json"
    app.refresh()


entry.on_submit = add_item
state["items"] = load()

theme_btn = Button("深色", variant="outline", on_click=toggle_theme)

for i, name in enumerate(CATS):
    nav_items.append(NavItem(name, active=(i == 0), w="fill",
                             on_click=(lambda k: (lambda: pick_cat(k)))(i)))

sidebar = Column(
    [Text("灵感库", size=F.sm, color="text_faint", padding=(S.s1, S.s3, S.s2, S.s3))]
    + nav_items
    + [Spacer(grow=True)],
    w=210, bg="surface", padding=(S.s4, S.s3), gap=S.s1,
)
sidebar.h = "fill"

sidebar_col = Column([sidebar], bg="surface_alt")
sidebar_col.h = "fill"

app.mount(Row(align="stretch", children=[
    sidebar_col,
    Column(grow=True, padding=S.s5, gap=S.s4, children=[
        Row(w="fill", children=[
            Text("灵感速记", size=F.xxl),
            Spacer(grow=True),
            theme_btn,
        ]),
        Row(w="fill", gap=S.s2, children=[
            entry,
            Button("记下", on_click=lambda: add_item(entry.value)),
        ]),
        stat,
        list_col,
    ]),
]))

rebuild()

if __name__ == "__main__":
    if "--shot" in sys.argv:
        out = sys.argv[sys.argv.index("--shot") + 1]
        app.run(after=lambda a: a.screenshot(out))
    else:
        app.run()
