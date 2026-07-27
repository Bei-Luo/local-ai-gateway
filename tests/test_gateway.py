import json

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
        "alias": "work-model",
        "upstream_model": "vendor-model-v2",
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
        assert "secret" not in json.dumps(route)

        response = client.put(
            f"/admin/api/routes/{route['id']}",
            json={
                "alias": "renamed-model",
                "upstream_model": "vendor-model-v3",
                "base_url": "https://other.example/v1",
                "api_key": None,
                "enabled": False,
            },
        )
        assert response.status_code == 200
        assert response.json()["api_key_masked"] == "sk-********-key"

        assert client.delete(f"/admin/api/routes/{route['id']}").status_code == 204
        assert client.get("/admin/api/routes").json() == []


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


def test_unknown_model_returns_openai_style_error(tmp_path):
    app = create_app(tmp_path / "gateway.db", httpx.AsyncClient())

    with TestClient(app) as client:
        response = client.post("/v1/responses", json={"model": "missing"})

    assert response.status_code == 404
    assert response.json()["error"]["type"] == "model_not_found"
