check:
	ruff check
	mypy src tests
	pytest

install:
	ln -fs ../../pre-commit-hook .git/hooks/pre-commit

.PHONY: check
