# minishare — dev helpers. `make test` is the definition of done.
#
# The venv is a real directory target: it rebuilds only when the
# dependency declaration changes, so `make test` is cheap to repeat.

VENV := .venv
PY   := $(VENV)/bin/python
PORT ?= 8000
DIR  ?= data

.PHONY: all test run clean

all: test

$(VENV): pyproject.toml
	python3 -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet --editable ".[dev]"
	touch $(VENV)

test: $(VENV)
	$(PY) -m pytest

# Dev server. Override like: make run PORT=9000 DIR=/srv/files
run: $(VENV)
	$(PY) -m minishare -p $(PORT) -d $(DIR)

clean:
	rm -rf $(VENV) .pytest_cache *.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
