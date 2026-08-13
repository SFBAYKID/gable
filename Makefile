# Gable — developer and deploy entry points.
#
# Everything below runs locally except `deploy`, which drives the droplet over
# SSH (key auth only — CLAUDE.md section 3). Nothing here handles a secret.

PYTHON  := .venv/bin/python
PIP     := .venv/bin/pip
RUFF    := .venv/bin/ruff
MYPY    := .venv/bin/mypy
PYTEST  := .venv/bin/pytest

# Droplet coordinates. Created 2026-08-10: "gable", Ubuntu 24.04 LTS, SFO3,
# 1 vCPU / 1 GB, $6/mo. Root-only for now; a deploy user comes with the first
# real deploy. The IP is not a secret — it is in DigitalOcean's console and on
# every packet — so it lives here rather than in .env.
GABLE_HOST ?= root@143.110.146.87
GABLE_DIR  ?= /opt/gable
GABLE_SSH_KEY ?= ~/.ssh/gable_droplet

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
	@echo "deploy     pull, install code + unit, reload systemd, restart"
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
	ssh -i $(GABLE_SSH_KEY) $(GABLE_HOST) "cd $(GABLE_DIR) && git pull --ff-only && \
		$(GABLE_DIR)/.venv/bin/pip install -e . && \
		install -d -o gable -g gable -m 0700 $(GABLE_DIR)/var && \
		find $(GABLE_DIR)/var -maxdepth 1 -type f -name 'gable.db*' -exec chmod 0600 {} \; && \
		install -d -o gable -g gable -m 0755 /var/www/gable-photos && \
		install -m 0644 deploy/gable.service /etc/systemd/system/gable.service && \
		systemctl daemon-reload && \
		systemctl restart gable"

logs:
	ssh -i $(GABLE_SSH_KEY) $(GABLE_HOST) "journalctl -u gable -f -o cat"

ssh:
	ssh -i $(GABLE_SSH_KEY) $(GABLE_HOST)
