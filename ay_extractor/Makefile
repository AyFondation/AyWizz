# Makefile — v15
# === ayExtractor Makefile ===
#
# Changelog:
#     v15: Fix Python inline IndentationError — ensure no leading
#          whitespace in python -c command within Makefile recipe.
#     v14: Rework health checks: Python TCP from dev container via
#          mapped ports on host.docker.internal.
#     v13: Use bash /dev/tcp for all checks (failed: Alpine has no bash).
#     v12: Fix qdrant — /dev/tcp (Debian slim, no curl/wget).
#     v11: Fix IPv6 — use 127.0.0.1 instead of localhost.
#     v10: Fix arangodb — use wget (Alpine, no curl).
#     v9: Add verbose diagnostic logging to health-check loop.
#     v8: Fix health-check in docker-outside-of-docker topology.
#     v7: Unified test lifecycle.
#     v6: Add install-all target.
#     v5: Add E2E test targets with docker-compose lifecycle management.
#     v4: Unified unit + integration coverage.
#     v3: Initial Makefile with separate targets.

PYTHON ?= python3
PYTEST ?= $(PYTHON) -m pytest
DOCKER ?= docker
SRC_DIR = src
TEST_DIR = tests
COV_DIR = coverage
TEST_COMPOSE = docker-compose.test.yml

# Host where mapped ports are reachable from the dev container.
# Docker Desktop exposes mapped ports via host.docker.internal.
TEST_HOST ?= host.docker.internal

.PHONY: help install install-all \
        test test-unit test-integration test-e2e test-gpu \
        test-coverage test-coverage-unit \
        test-infra-up test-infra-down test-infra-status \
        lint format typecheck clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}'

install:  ## Install project in editable mode with dev dependencies
	$(PYTHON) -m pip install -e ".[dev]" --break-system-packages

install-all:  ## Install project with ALL dependencies (runtime + dev + integration)
	$(PYTHON) -m pip install -e ".[all,dev,integration]" --break-system-packages

# ── Infrastructure lifecycle ────────────────────────────────────

test-infra-up:  ## Start ALL test infrastructure (ArangoDB, Qdrant, ChromaDB, Ollama)
	@echo "╔══════════════════════════════════════════════╗"
	@echo "║  Starting test infrastructure...             ║"
	@echo "╚══════════════════════════════════════════════╝"
	$(DOCKER) compose -f $(TEST_COMPOSE) up -d
	@echo ""
	@echo "Waiting for services to be ready (via $(TEST_HOST))..."
	@for entry in \
		"ayextractor-test-arangodb|8529" \
		"ayextractor-test-qdrant|6333" \
		"ayextractor-test-chromadb|8000" \
		"ayextractor-test-ollama|11434" \
	; do \
		name=$$(echo "$$entry" | cut -d'|' -f1); \
		port=$$(echo "$$entry" | cut -d'|' -f2); \
		echo ""; \
		echo "── $$name (:$$port) ──"; \
		state=$$($(DOCKER) inspect -f '{{.State.Status}}' $$name 2>&1); \
		echo "   container state: $$state"; \
		if [ "$$state" != "running" ]; then \
			echo "   ✗ CONTAINER NOT RUNNING"; \
			echo "   docker logs (last 15 lines):"; \
			$(DOCKER) logs --tail 15 $$name 2>&1 | sed 's/^/     /'; \
			exit 1; \
		fi; \
		ok=0; \
		last_err=""; \
		for i in $$(seq 1 60); do \
			last_err=$$($(PYTHON) -c "import socket;s=socket.create_connection(('$(TEST_HOST)',$$port),timeout=2);s.close()" 2>&1); \
			if [ $$? -eq 0 ]; then \
				echo "   ✓ ready (attempt $$i/60)"; \
				ok=1; \
				break; \
			fi; \
			if [ $$i -le 3 ] || [ $$(( $$i % 10 )) -eq 0 ]; then \
				echo "   [attempt $$i/60] err: $$last_err"; \
			fi; \
			sleep 2; \
		done; \
		if [ $$ok -eq 0 ]; then \
			echo "   ✗ TIMEOUT after 120s"; \
			echo "   last err: $$last_err"; \
			echo "   docker logs (last 25 lines):"; \
			$(DOCKER) logs --tail 25 $$name 2>&1 | sed 's/^/     /'; \
			exit 1; \
		fi; \
	done
	@echo ""
	@echo "Waiting for Ollama model pull..."
	@$(DOCKER) wait ayextractor-test-ollama-init 2>/dev/null || true
	@echo ""
	@echo "✓ All services ready."
	@echo ""

test-infra-down:  ## Stop ALL test infrastructure and cleanup volumes
	$(DOCKER) compose -f $(TEST_COMPOSE) down -v
	@echo "✓ Test infrastructure stopped."

test-infra-status:  ## Show status of test infrastructure containers
	@echo "Test infrastructure status:"
	@$(DOCKER) inspect -f '  {{.Name}}: {{.State.Status}} ({{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}})' \
		ayextractor-test-arangodb ayextractor-test-qdrant \
		ayextractor-test-chromadb ayextractor-test-ollama 2>/dev/null \
		|| echo "  (no containers running)"

# ── Test targets ────────────────────────────────────────────────

test:  ## Run ALL tests (infra up → unit + integration + e2e → infra down)
	@$(MAKE) test-infra-up
	@echo "╔══════════════════════════════════════════════╗"
	@echo "║  Running all tests...                        ║"
	@echo "╚══════════════════════════════════════════════╝"
	@$(PYTEST) $(TEST_DIR)/unit/ $(TEST_DIR)/integration/ $(TEST_DIR)/e2e/ \
		-v --tb=short; \
		EXIT_CODE=$$?; \
		$(MAKE) test-infra-down; \
		exit $$EXIT_CODE

test-unit:  ## Run unit tests only (no Docker required)
	$(PYTEST) $(TEST_DIR)/unit/ -v --tb=short

test-integration:  ## Run integration tests only (requires running infra or Docker)
	$(PYTEST) $(TEST_DIR)/integration/ -v --tb=short

test-e2e:  ## Run E2E tests only (requires running infra)
	$(PYTEST) $(TEST_DIR)/e2e/ -v --tb=short

test-gpu:  ## Run GPU-accelerated tests (requires NVIDIA GPU + RAPIDS)
	$(PYTEST) $(TEST_DIR)/ -v --tb=short -m gpu

# ── Coverage targets ────────────────────────────────────────────

test-coverage:  ## Run ALL tests with coverage (infra up → tests → infra down)
	@$(MAKE) test-infra-up
	@mkdir -p $(COV_DIR)
	@echo "╔══════════════════════════════════════════════╗"
	@echo "║  Running all tests with coverage...          ║"
	@echo "╚══════════════════════════════════════════════╝"
	@$(PYTEST) $(TEST_DIR)/unit/ $(TEST_DIR)/integration/ $(TEST_DIR)/e2e/ \
		--cov=$(SRC_DIR) \
		--cov-report=term-missing \
		--cov-report=html:$(COV_DIR)/html \
		--cov-report=xml:$(COV_DIR)/coverage.xml \
		--cov-report=json:$(COV_DIR)/coverage.json \
		-v --tb=short; \
		EXIT_CODE=$$?; \
		$(MAKE) test-infra-down; \
		echo ""; \
		echo "Coverage reports generated in $(COV_DIR)/"; \
		echo "  HTML  → $(COV_DIR)/html/index.html"; \
		echo "  XML   → $(COV_DIR)/coverage.xml"; \
		echo "  JSON  → $(COV_DIR)/coverage.json"; \
		exit $$EXIT_CODE

test-coverage-unit:  ## Run unit tests only with coverage report
	@mkdir -p $(COV_DIR)
	$(PYTEST) $(TEST_DIR)/unit/ \
		--cov=$(SRC_DIR) \
		--cov-report=term-missing \
		--cov-report=html:$(COV_DIR)/html \
		--cov-report=xml:$(COV_DIR)/coverage.xml \
		-v --tb=short

# ── Code quality ────────────────────────────────────────────────

lint:  ## Run linters (ruff)
	$(PYTHON) -m ruff check $(SRC_DIR) $(TEST_DIR)

format:  ## Auto-format code (ruff)
	$(PYTHON) -m ruff format $(SRC_DIR) $(TEST_DIR)

typecheck:  ## Run type checker (mypy)
	$(PYTHON) -m mypy $(SRC_DIR)

clean:  ## Remove build artifacts, caches, coverage
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache htmlcov/ .coverage $(COV_DIR)/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true