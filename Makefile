check:
	ruff check src tests
	ruff format --check src tests
	mypy src tests

test:
	pytest

bump-patch: check test
	bumpver update --patch

bump-minor: check test
	bumpver update --minor

bump-major: check test
	bumpver update --major

publish: check test
	uv build
	uv publish

.PHONY: check test bump-patch bump-minor bump-major publish
