.PHONY: help install test lint fmt type check golden bench clean

PYTHON ?= python
RUN := $(PYTHON)

help:                       ## 显示可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:                    ## 安装开发依赖（可编辑模式）
	$(RUN) -m pip install -e ".[dev]"

test:                       ## 跑单元测试（无需窗口）
	$(RUN) -m pytest tests/unit -q

test-all:                   ## 跑全部测试（含黄金图与平台测试）
	$(RUN) -m pytest -q

golden:                     ## 更新黄金图基线
	$(RUN) -m pytest tests/golden -q --update-goldens

lint:                       ## 静态检查
	$(RUN) -m ruff check src tests
	$(RUN) -m ruff format --check src tests

fmt:                        ## 自动格式化
	$(RUN) -m ruff check --fix src tests
	$(RUN) -m ruff format src tests

type:                       ## 类型检查（严格模式）
	$(RUN) -m mypy

check: lint type test       ## 提交前必跑：lint + 类型 + 测试

bench:                      ## 性能基准
	$(RUN) -m pytest benchmarks -q --benchmark-only

clean:                      ## 清理构建与缓存产物
	rm -rf build dist .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
