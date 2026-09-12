.PHONY: help install test lint fmt type type-all check golden cov perf arch bench clean

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

golden:                     ## 更新黄金图基线（改过渲染相关代码后必须重跑并肉眼确认）
	INKSTONE_UPDATE_GOLDEN=1 $(RUN) -m pytest tests/unit/test_golden_form.py tests/unit/test_text_render.py -q

lint:                       ## 静态检查
	$(RUN) -m ruff check src tests examples benchmarks
	$(RUN) -m ruff format --check src tests examples benchmarks

fmt:                        ## 自动格式化
	$(RUN) -m ruff check --fix src tests examples benchmarks
	$(RUN) -m ruff format src tests examples benchmarks

type:                       ## 类型检查（严格模式）
	$(RUN) -m mypy

type-all:                   ## 类型检查 × 三平台（平台专有代码也能过一遍）
	$(RUN) -m mypy
	$(RUN) -m mypy --platform win32
	$(RUN) -m mypy --platform darwin

check: lint type test       ## 提交前必跑：lint + 类型 + 测试

cov:                        ## 覆盖率门禁（排除 slow：插桩会让性能断言失真）
	$(RUN) -m pytest tests -q -m "not slow" \
		--cov=src/inkstone --cov-report=term-missing --cov-fail-under=85
perf:                       ## 性能预算（无插桩才准）
	$(RUN) -m pytest tests -q -m "slow"

arch:                       ## 架构约束：分层单向 / text 层无平台 API / 组件零硬编码
	$(RUN) -m pytest tests/unit/test_architecture.py -q

bench:                      ## 帧耗时基准（R7.5 三场景 p50/p95，驱动 GL 决策）
	$(RUN) benchmarks/run.py

clean:                      ## 清理构建与缓存产物
	rm -rf build dist .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
