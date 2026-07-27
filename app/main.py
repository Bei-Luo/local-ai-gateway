import json
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
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

    alias: str = Field(min_length=1, max_length=128)
    upstream_model: str = Field(min_length=1, max_length=256)
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


def mask_key(api_key: str) -> str:
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:3]}{'*' * 8}{api_key[-4:]}"


def public_route(route: dict) -> dict:
    return {
        "id": route["id"],
        "alias": route["alias"],
        "upstream_model": route["upstream_model"],
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
        expected = os.getenv("GATEWAY_API_KEY", "").strip()
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

    @app.get("/v1/models", dependencies=[Depends(require_gateway_key)])
    async def models() -> dict:
        routes = [route for route in store.list_routes() if route["enabled"]]
        return {
            "object": "list",
            "data": [
                {
                    "id": route["alias"],
                    "object": "model",
                    "created": 0,
                    "owned_by": "local-ai-gateway",
                }
                for route in routes
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
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return openai_error("Request body must be valid JSON", 400, "invalid_request_error")

        alias = body.get("model") if isinstance(body, dict) else None
        if not isinstance(alias, str) or not alias:
            return openai_error("Request body must include a model", 400, "invalid_request_error")

        route = store.get_by_alias(alias)
        if not route:
            return openai_error(
                f"No enabled route is configured for model '{alias}'",
                404,
                "model_not_found",
            )

        body["model"] = route["upstream_model"]
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
            return openai_error(f"Upstream request failed: {exc}", 502, "upstream_error")

        response_headers = {
            name: value
            for name, value in upstream_response.headers.items()
            if name.lower() not in HOP_BY_HOP_HEADERS
        }
        return StreamingResponse(
            upstream_response.aiter_raw(),
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
