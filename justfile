default: fmt lint isort

fmt:
    uv run ruff format .

lint:
    uv run ruff check --fix .

isort:
    uv run ruff check --select I --fix .

check:
    uv run ty check .
    uv run ruff format --check .
    uv run ruff check --extend-ignore I .
    uv run ruff check --select I .

test:
    uv run pytest tests -q
