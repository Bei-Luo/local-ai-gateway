import json
import sqlite3

import httpx
from fastapi.testclient import TestClient

from app.main import create_app


class AsyncChunks(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


def add_route(client: TestClient, **overrides) -> dict:
    payload = {
        "site_name": "Change2Pro",
        "alias": "work-model",
        "upstream_model": "vendor-model-v2",
        "note": "Primary coding route",
        "base_url": "https://upstream.example/v1",
        "api_key": "sk-secret-route-key",
        "enabled": True,
    }
    payload.update(overrides)
    response = client.post("/admin/api/routes", json=payload)
    assert response.status_code == 201
    return response.json()


def test_route_crud_masks_api_key(tmp_path):
    app = create_app(tmp_path / "gateway.db", httpx.AsyncClient())

    with TestClient(app) as client:
        route = add_route(client)
        assert route["api_key_masked"] == "sk-********-key"
        assert route["site_name"] == "Change2Pro"
        assert route["note"] == "Primary coding route"
        assert "secret" not in json.dumps(route)

        response = client.put(
            f"/admin/api/routes/{route['id']}",
            json={
                "site_name": "Backup Provider",
                "alias": "renamed-model",
                "upstream_model": "vendor-model-v3",
                "note": "Backup route",
                "base_url": "https://other.example/v1",
                "api_key": None,
                "enabled": False,
            },
        )
        assert response.status_code == 200
        assert response.json()["api_key_masked"] == "sk-********-key"
        assert response.json()["site_name"] == "Backup Provider"
        assert response.json()["note"] == "Backup route"

        assert client.delete(f"/admin/api/routes/{route['id']}").status_code == 204
        assert client.get("/admin/api/routes").json() == []


def test_existing_database_is_migrated_with_note_column(tmp_path):
    database = tmp_path / "gateway.db"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE model_routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alias TEXT NOT NULL UNIQUE COLLATE NOCASE,
            upstream_model TEXT NOT NULL,
            base_url TEXT NOT NULL,
            api_key TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.commit()
    connection.close()

    app = create_app(database, httpx.AsyncClient())
    with TestClient(app) as client:
        route = add_route(client, note="Migrated database")
        duplicate = add_route(
            client,
            site_name="Second provider",
            upstream_model="same-alias-other-model",
            api_key="sk-second-route-key",
        )

    assert route["note"] == "Migrated database"
    assert duplicate["alias"] == route["alias"]


def test_proxy_rewrites_model_and_authorization(tmp_path):
    observed = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["authorization"] = request.headers["authorization"]
        observed["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=AsyncChunks(
                b'{"id":"chatcmpl-1","choices":[{"message":{"content":"ok"}}]}'
            ),
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = create_app(tmp_path / "gateway.db", upstream_client)

    with TestClient(app) as client:
        add_route(client)
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer local-gateway-key"},
            json={"model": "work-model", "messages": [{"role": "user", "content": "Hi"}]},
        )

        assert response.status_code == 200
        assert observed == {
            "url": "https://upstream.example/v1/chat/completions",
            "authorization": "Bearer sk-secret-route-key",
            "body": {
                "model": "vendor-model-v2",
                "messages": [{"role": "user", "content": "Hi"}],
            },
        }
        usage = client.get("/admin/api/usage").json()
        assert len(usage) == 1
        assert usage[0]["model_alias"] == "work-model"
        assert usage[0]["site_name"] == "Change2Pro"
        assert usage[0]["path"] == "/v1/chat/completions"
        assert usage[0]["request_type"] == "Chat"
        assert usage[0]["streamed"] == 0
        assert usage[0]["status_code"] == 200
        assert usage[0]["ttft_ms"] is not None
        assert usage[0]["duration_ms"] >= usage[0]["ttft_ms"]


def test_generated_gateway_token_protects_v1_routes(tmp_path):
    app = create_app(tmp_path / "gateway.db", httpx.AsyncClient())

    with TestClient(app) as client:
        assert client.get("/admin/api/gateway-token").json()["configured"] is False
        generated = client.post("/admin/api/gateway-token").json()
        token = generated["token"]
        assert token not in client.get("/admin/api/gateway-token").text

        assert client.get("/v1/models").status_code == 401
        response = client.get(
            "/v1/models",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

        assert client.delete("/admin/api/gateway-token").status_code == 204
        assert client.get("/v1/models").status_code == 200


def test_usage_records_can_be_cleared(tmp_path):
    app = create_app(tmp_path / "gateway.db", httpx.AsyncClient())

    with TestClient(app) as client:
        response = client.post("/v1/responses", json={"model": "missing"})
        assert response.status_code == 404
        assert client.get("/admin/api/usage").json()[0]["status_code"] == 404
        assert client.delete("/admin/api/usage").status_code == 204
        assert client.get("/admin/api/usage").json() == []


def test_models_only_lists_enabled_routes(tmp_path):
    app = create_app(tmp_path / "gateway.db", httpx.AsyncClient())

    with TestClient(app) as client:
        add_route(client)
        add_route(
            client,
            alias="disabled-model",
            api_key="sk-other-key",
            enabled=False,
        )
        result = client.get("/v1/models").json()

    assert [model["id"] for model in result["data"]] == ["work-model"]


def test_route_can_be_disabled_and_enabled(tmp_path):
    app = create_app(tmp_path / "gateway.db", httpx.AsyncClient())

    with TestClient(app) as client:
        route = add_route(client)
        response = client.patch(
            f"/admin/api/routes/{route['id']}/enabled",
            json={"enabled": False},
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is False
        assert client.get("/v1/models").json()["data"] == []

        response = client.patch(
            f"/admin/api/routes/{route['id']}/enabled",
            json={"enabled": True},
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is True
        assert client.get("/v1/models").json()["data"][0]["id"] == "work-model"


def test_duplicate_aliases_switch_the_active_upstream(tmp_path):
    observed = []

    def upstream(request: httpx.Request) -> httpx.Response:
        observed.append((str(request.url), request.headers["authorization"]))
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=AsyncChunks(b'{"choices":[]}'),
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = create_app(tmp_path / "gateway.db", upstream_client)

    with TestClient(app) as client:
        first = add_route(client, site_name="Provider A")
        second = add_route(
            client,
            site_name="Provider B",
            upstream_model="provider-b-model",
            base_url="https://provider-b.example/v1",
            api_key="sk-provider-b-key",
        )

        routes = client.get("/admin/api/routes").json()
        assert [(route["site_name"], route["enabled"]) for route in routes] == [
            ("Provider A", False),
            ("Provider B", True),
        ]
        assert [model["id"] for model in client.get("/v1/models").json()["data"]] == [
            "work-model"
        ]

        response = client.post(
            "/v1/chat/completions",
            json={"model": "work-model", "messages": []},
        )
        assert response.status_code == 200
        assert observed[-1] == (
            "https://provider-b.example/v1/chat/completions",
            "Bearer sk-provider-b-key",
        )

        response = client.patch(
            f"/admin/api/routes/{first['id']}/enabled",
            json={"enabled": True},
        )
        assert response.status_code == 200
        routes = client.get("/admin/api/routes").json()
        assert [(route["site_name"], route["enabled"]) for route in routes] == [
            ("Provider A", True),
            ("Provider B", False),
        ]

        response = client.post(
            "/v1/chat/completions",
            json={"model": "work-model", "messages": []},
        )
        assert response.status_code == 200
        assert observed[-1] == (
            "https://upstream.example/v1/chat/completions",
            "Bearer sk-secret-route-key",
        )


def test_discover_models_uses_existing_route_key(tmp_path):
    observed = []

    def upstream(request: httpx.Request) -> httpx.Response:
        observed.append((str(request.url), request.headers["authorization"]))
        if request.url.path == "/models":
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "model-z", "object": "model"},
                    {"id": "model-a", "object": "model"},
                    {"id": "model-a", "object": "model"},
                ],
            },
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = create_app(tmp_path / "gateway.db", upstream_client)

    with TestClient(app) as client:
        route = add_route(client)
        response = client.post(
            "/admin/api/discover-models",
            json={
                "base_url": "https://upstream.example/",
                "api_key": None,
                "route_id": route["id"],
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "models": ["model-a", "model-z"],
        "base_url": "https://upstream.example/v1",
    }
    assert observed == [
        ("https://upstream.example/models", "Bearer sk-secret-route-key"),
        ("https://upstream.example/v1/models", "Bearer sk-secret-route-key"),
    ]


def test_streaming_response_is_forwarded(tmp_path):
    def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=AsyncChunks(
                b'data: {"delta":"hello"}\n\n',
                b"data: [DONE]\n\n",
            ),
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = create_app(tmp_path / "gateway.db", upstream_client)

    with TestClient(app) as client:
        add_route(client)
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={"model": "work-model", "stream": True, "messages": []},
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert "data: [DONE]" in "".join(response.iter_text())
        usage = client.get("/admin/api/usage").json()
        assert usage[0]["site_name"] == "Change2Pro"
        assert usage[0]["request_type"] == "Chat"
        assert usage[0]["streamed"] == 1
        assert usage[0]["ttft_ms"] is not None
        assert usage[0]["duration_ms"] >= usage[0]["ttft_ms"]


def test_unknown_model_returns_openai_style_error(tmp_path):
    app = create_app(tmp_path / "gateway.db", httpx.AsyncClient())

    with TestClient(app) as client:
        response = client.post("/v1/responses", json={"model": "missing"})

    assert response.status_code == 404
    assert response.json()["error"]["type"] == "model_not_found"
