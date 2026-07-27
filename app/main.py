import json
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.background import BackgroundTask

from app.store import RouteStore


ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "app" / "static"
ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


class RoutePayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    site_name: str = Field(default="", max_length=128)
    alias: str = Field(min_length=1, max_length=128)
    upstream_model: str = Field(min_length=1, max_length=256)
    note: str = Field(default="", max_length=256)
    base_url: str = Field(min_length=8, max_length=2048)
    api_key: str | None = Field(default=None, max_length=4096)
    enabled: bool = True

    @field_validator("alias")
    @classmethod
    def validate_alias(cls, value: str) -> str:
        if not ALIAS_PATTERN.fullmatch(value):
            raise ValueError("Use letters, numbers, dot, slash, colon, underscore, or hyphen")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = httpx.URL(value)
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            raise ValueError("Base URL must be an absolute HTTP(S) URL")
        return value.rstrip("/")


class DiscoverModelsPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    base_url: str = Field(min_length=8, max_length=2048)
    api_key: str | None = Field(default=None, max_length=4096)
    route_id: int | None = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return RoutePayload.validate_base_url(value)


class RouteEnabledPayload(BaseModel):
    enabled: bool


def mask_key(api_key: str) -> str:
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:3]}{'*' * 8}{api_key[-4:]}"


def public_route(route: dict) -> dict:
    return {
        "id": route["id"],
        "site_name": route["site_name"],
        "alias": route["alias"],
        "upstream_model": route["upstream_model"],
        "note": route["note"],
        "base_url": route["base_url"],
        "api_key_masked": mask_key(route["api_key"]),
        "enabled": bool(route["enabled"]),
        "created_at": route["created_at"],
        "updated_at": route["updated_at"],
    }


def openai_error(message: str, status_code: int, error_type: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": error_type, "code": None}},
    )


def request_type_for_path(path: str) -> str:
    if path.endswith("chat/completions"):
        return "Chat"
    if path.endswith("responses"):
        return "Responses"
    if path.endswith("embeddings"):
        return "Embeddings"
    return path.rsplit("/", 1)[-1] or "Unknown"


def create_app(
    db_path: Path | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    database = db_path or Path(os.getenv("GATEWAY_DB_PATH", ROOT / "data" / "gateway.db"))
    store = RouteStore(database)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.client = http_client or httpx.AsyncClient(timeout=None)
        app.state.owns_client = http_client is None
        yield
        if app.state.owns_client:
            await app.state.client.aclose()
        store.close()

    app = FastAPI(title="Local AI Gateway", version="0.1.0", lifespan=lifespan)
    app.state.store = store

    def require_gateway_key(request: Request) -> None:
        expected = os.getenv("GATEWAY_API_KEY", "").strip() or store.get_setting(
            "gateway_api_key"
        )
        if not expected:
            return
        supplied = request.headers.get("authorization", "")
        if supplied != f"Bearer {expected}":
            raise HTTPException(status_code=401, detail="Invalid gateway API key")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/admin/api/routes")
    async def list_routes() -> list[dict]:
        return [public_route(route) for route in store.list_routes()]

    @app.post("/admin/api/routes", status_code=201)
    async def create_route(payload: RoutePayload) -> dict:
        if not payload.api_key:
            raise HTTPException(status_code=422, detail="API key is required")
        try:
            route = store.create_route(payload.model_dump())
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Model alias already exists") from exc
        return public_route(route)

    @app.put("/admin/api/routes/{route_id}")
    async def update_route(route_id: int, payload: RoutePayload) -> dict:
        try:
            route = store.update_route(route_id, payload.model_dump())
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Model alias already exists") from exc
        if not route:
            raise HTTPException(status_code=404, detail="Route not found")
        return public_route(route)

    @app.delete("/admin/api/routes/{route_id}", status_code=204)
    async def delete_route(route_id: int) -> None:
        if not store.delete_route(route_id):
            raise HTTPException(status_code=404, detail="Route not found")

    @app.patch("/admin/api/routes/{route_id}/enabled")
    async def set_route_enabled(route_id: int, payload: RouteEnabledPayload) -> dict:
        route = store.set_enabled(route_id, payload.enabled)
        if not route:
            raise HTTPException(status_code=404, detail="Route not found")
        return public_route(route)

    @app.get("/admin/api/gateway-token")
    async def gateway_token_status() -> dict:
        environment_token = os.getenv("GATEWAY_API_KEY", "").strip()
        generated_token = store.get_setting("gateway_api_key")
        token = environment_token or generated_token
        return {
            "configured": bool(token),
            "masked": mask_key(token) if token else None,
            "source": "environment" if environment_token else "generated" if token else "none",
        }

    @app.post("/admin/api/gateway-token")
    async def generate_gateway_token() -> dict:
        if os.getenv("GATEWAY_API_KEY", "").strip():
            raise HTTPException(
                status_code=409,
                detail="Gateway token is managed by GATEWAY_API_KEY",
            )
        token = secrets.token_urlsafe(32)
        store.set_setting("gateway_api_key", token)
        return {"token": token, "masked": mask_key(token), "source": "generated"}

    @app.delete("/admin/api/gateway-token", status_code=204)
    async def disable_gateway_token() -> None:
        if os.getenv("GATEWAY_API_KEY", "").strip():
            raise HTTPException(
                status_code=409,
                detail="Gateway token is managed by GATEWAY_API_KEY",
            )
        store.delete_setting("gateway_api_key")

    @app.get("/admin/api/usage")
    async def usage_records(limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
        return store.list_usage(limit)

    @app.delete("/admin/api/usage", status_code=204)
    async def clear_usage_records() -> None:
        store.clear_usage()

    @app.post("/admin/api/discover-models")
    async def discover_models(payload: DiscoverModelsPayload) -> dict:
        api_key = payload.api_key
        if not api_key and payload.route_id is not None:
            route = store.get_route(payload.route_id)
            if not route:
                raise HTTPException(status_code=404, detail="Route not found")
            api_key = route["api_key"]
        if not api_key:
            raise HTTPException(status_code=422, detail="API key is required")

        candidate_base_urls = [payload.base_url]
        if not httpx.URL(payload.base_url).path.rstrip("/").endswith("/v1"):
            candidate_base_urls.append(f"{payload.base_url}/v1")

        failures = []
        for base_url in candidate_base_urls:
            try:
                response = await app.state.client.get(
                    f"{base_url}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=15,
                )
            except httpx.RequestError as exc:
                failures.append(str(exc))
                continue

            if not response.is_success:
                failures.append(f"HTTP {response.status_code}")
                continue
            try:
                data = response.json().get("data")
            except (json.JSONDecodeError, AttributeError):
                failures.append("invalid JSON")
                continue
            if not isinstance(data, list):
                failures.append("missing model list")
                continue

            model_ids = sorted(
                {
                    item["id"]
                    for item in data
                    if isinstance(item, dict)
                    and isinstance(item.get("id"), str)
                    and item["id"]
                },
                key=str.casefold,
            )
            return {"models": model_ids, "base_url": base_url}

        reason = ", ".join(failures) or "unknown error"
        raise HTTPException(status_code=502, detail=f"Unable to discover models: {reason}")

    @app.get("/v1/models", dependencies=[Depends(require_gateway_key)])
    async def models() -> dict:
        routes = [route for route in store.list_routes() if route["enabled"]]
        unique_routes = {route["alias"].casefold(): route for route in routes}
        return {
            "object": "list",
            "data": [
                {
                    "id": route["alias"],
                    "object": "model",
                    "created": 0,
                    "owned_by": "local-ai-gateway",
                }
                for route in unique_routes.values()
            ],
        }

    @app.get("/v1/models/{alias:path}", dependencies=[Depends(require_gateway_key)])
    async def model(alias: str) -> dict:
        route = store.get_by_alias(alias)
        if not route:
            raise HTTPException(status_code=404, detail="Model not found")
        return {
            "id": route["alias"],
            "object": "model",
            "created": 0,
            "owned_by": "local-ai-gateway",
        }

    @app.api_route(
        "/v1/{upstream_path:path}",
        methods=["POST", "PUT", "PATCH", "DELETE"],
        dependencies=[Depends(require_gateway_key)],
    )
    async def proxy(upstream_path: str, request: Request):
        started_at = time.perf_counter()
        request_path = f"/v1/{upstream_path}"
        request_type = request_type_for_path(request_path)

        def record_usage(
            status_code: int,
            alias: str = "",
            upstream_model: str = "",
            site_name: str = "",
            streamed: bool = False,
            ttft_ms: int | None = None,
        ) -> None:
            store.record_usage(
                alias,
                upstream_model,
                site_name,
                request_path,
                request_type,
                streamed,
                status_code,
                ttft_ms,
                round((time.perf_counter() - started_at) * 1000),
            )

        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            record_usage(400)
            return openai_error("Request body must be valid JSON", 400, "invalid_request_error")

        alias = body.get("model") if isinstance(body, dict) else None
        if not isinstance(alias, str) or not alias:
            record_usage(400)
            return openai_error("Request body must include a model", 400, "invalid_request_error")

        route = store.get_by_alias(alias)
        if not route:
            record_usage(404, alias)
            return openai_error(
                f"No enabled route is configured for model '{alias}'",
                404,
                "model_not_found",
            )

        body["model"] = route["upstream_model"]
        streamed = body.get("stream") is True
        headers = {
            name: value
            for name, value in request.headers.items()
            if name.lower() not in HOP_BY_HOP_HEADERS and name.lower() != "authorization"
        }
        headers["authorization"] = f"Bearer {route['api_key']}"
        headers["content-type"] = "application/json"
        upstream_url = f"{route['base_url']}/{upstream_path}"

        try:
            upstream_request = app.state.client.build_request(
                request.method,
                upstream_url,
                headers=headers,
                content=json.dumps(body).encode("utf-8"),
            )
            upstream_response = await app.state.client.send(upstream_request, stream=True)
        except httpx.RequestError as exc:
            record_usage(
                502,
                alias,
                route["upstream_model"],
                route["site_name"],
                streamed,
            )
            return openai_error(f"Upstream request failed: {exc}", 502, "upstream_error")

        async def stream_and_record():
            ttft_ms = None
            try:
                async for chunk in upstream_response.aiter_raw():
                    if ttft_ms is None:
                        ttft_ms = round((time.perf_counter() - started_at) * 1000)
                    yield chunk
            finally:
                record_usage(
                    upstream_response.status_code,
                    alias,
                    route["upstream_model"],
                    route["site_name"],
                    streamed,
                    ttft_ms,
                )

        response_headers = {
            name: value
            for name, value in upstream_response.headers.items()
            if name.lower() not in HOP_BY_HOP_HEADERS
        }
        return StreamingResponse(
            stream_and_record(),
            status_code=upstream_response.status_code,
            headers=response_headers,
            background=BackgroundTask(upstream_response.aclose),
        )

    app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(ASSETS / "index.html")

    return app


app = create_app()


def run() -> None:
    uvicorn.run("app.main:app", host="127.0.0.1", port=8787)


if __name__ == "__main__":
    run()
