.PHONY: install test

install:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -U pip && pip install -e '.[dev]'

test:
	. .venv/bin/activate && pytest
