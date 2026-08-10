# Gable — developer and deploy entry points.
#
# Everything below runs locally except `deploy`, which drives the droplet over
# SSH (key auth only — CLAUDE.md section 3). Nothing here handles a secret.

PYTHON  := .venv/bin/python
PIP     := .venv/bin/pip
RUFF    := .venv/bin/ruff
MYPY    := .venv/bin/mypy
PYTEST  := .venv/bin/pytest

# Droplet coordinates. Override on the command line, never commit real values:
#   make deploy GABLE_HOST=gable@203.0.113.10
GABLE_HOST ?= gable@CHANGE-ME
GABLE_DIR  ?= /opt/gable

.DEFAULT_GOAL := check
.PHONY: help setup fmt lint typecheck test check run clean deploy logs

help:
	@echo "setup      create .venv and install the package plus dev tools"
	@echo "fmt        format and auto-fix with ruff"
	@echo "lint       ruff check (no fixes)"
	@echo "typecheck  mypy --strict"
	@echo "test       pytest (unit only; integration tests skip without creds)"
	@echo "check      lint + typecheck + test  <- the definition-of-done gate"
	@echo "run        run the pipeline locally without Slack (cli.py)"
	@echo "deploy     git pull + systemctl restart on the droplet"
	@echo "logs       tail Gable's journald output on the droplet"

setup:
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

fmt:
	$(RUFF) format .
	$(RUFF) check --fix .

lint:
	$(RUFF) format --check .
	$(RUFF) check .

typecheck:
	$(MYPY)

test:
	$(PYTEST)

check: lint typecheck test

run:
	$(PYTHON) -m gable.cli

clean:
	rm -rf .mypy_cache .ruff_cache .pytest_cache build dist
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +

# Deploy is a pull, never a push of local files. No hand-editing on the server
# (CLAUDE.md section 9).
deploy:
	ssh $(GABLE_HOST) "cd $(GABLE_DIR) && git pull --ff-only && \
		$(GABLE_DIR)/.venv/bin/pip install -e . && \
		sudo systemctl restart gable"

logs:
	ssh $(GABLE_HOST) "journalctl -u gable -f -o cat"
