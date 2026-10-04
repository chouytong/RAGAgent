.PHONY: check test up
check:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy src
	npm --prefix frontend run lint
	npm --prefix frontend run check
test:
	uv run pytest -q
up:
	docker compose up --build
