black --check invoicez tests
isort --check invoicez tests
mypy invoicez tests
flake8 --count invoicez tests
pylint invoicez tests
