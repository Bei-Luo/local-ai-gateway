# Local AI Gateway

## Commands

- Install: `python -m venv .venv`, then `.venv\Scripts\python -m pip install -e ".[test]"`.
- Run locally: `.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8787`.
- Test all: `.venv\Scripts\python -m pytest`.
- Test one: `.venv\Scripts\python -m pytest tests/test_gateway.py::<test_name>`.

## Architecture

- `app/main.py` owns the FastAPI admin API, `/v1/models`, and the generic OpenAI-compatible `/v1/*` proxy. Keep streamed upstream responses streamed; do not eagerly read them.
- `app/store.py` is the SQLite persistence boundary for model routes, settings, and the latest 1000 usage records. Secrets are stored locally in plaintext but must only be returned by admin APIs as masks, except once when a new gateway token is generated.
- `app/static/` is a build-free same-origin admin UI; there is intentionally no Node runtime dependency.
- A route alias is the model ID exposed to OpenCode. Multiple routes may share an alias, but enabling one must atomically disable every other route with that alias. Before proxying, replace the alias with `upstream_model` and replace the incoming authorization header with that route's API key.

## Runtime Constraints

- Default state is `data/gateway.db`; tests must pass a temporary database to `create_app`.
- Keep the default bind address on `127.0.0.1`: the admin API has no login. `GATEWAY_API_KEY` overrides a UI-generated token; either protects `/v1/*`, not the admin UI.
- Upstreams must expose OpenAI-compatible paths beneath their configured Base URL. The gateway does not translate between vendor-specific protocols.
