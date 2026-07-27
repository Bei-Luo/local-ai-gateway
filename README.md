# Local AI Gateway

一个面向 OpenCode 等本地 AI Agent 的轻量级 OpenAI-compatible 网关。Agent 只连接一个本地提供商；网关按照请求中的模型名，选择对应的上游 Base URL、真实模型名和 API Key。

## 功能

- 一个本地提供商承载任意数量的模型别名
- 每个模型独立配置上游站点、真实模型名和 API Key
- 从上游 `/models` 检测并选择可用模型
- 生成本地网关 API 令牌，保护 OpenAI-compatible `/v1/*` 接口
- 记录最近 1000 次转发的模型、接口、状态码和响应耗时
- 透传 `/v1/*` JSON 请求，适用于 Chat Completions、Responses、Embeddings 等 OpenAI-compatible 接口
- 透传 SSE 流式响应
- 提供 `/v1/models` 模型目录
- 浏览器管理页面，API Key 默认只显示掩码
- SQLite 单文件存储，无前端构建工具

## 启动

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[test]"
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8787
```

打开 <http://127.0.0.1:8787> 配置模型路由。数据库默认位于 `data/gateway.db`。

可选环境变量：

- `GATEWAY_DB_PATH`：修改 SQLite 数据库位置。
- `GATEWAY_API_KEY`：要求 Agent 请求携带 `Authorization: Bearer <key>`。它优先于管理页面生成的令牌；两者都未设置时，本地代理接口无需认证。

服务应保持监听 `127.0.0.1`。如需暴露到局域网，必须先增加管理端认证和 TLS，不能直接修改为 `0.0.0.0` 后裸露运行。

## OpenCode 配置

先在管理页面创建例如 `work-sonnet` 和 `personal-gpt` 两条路由，再在项目或全局 `opencode.json` 中配置一个提供商：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "local-gateway": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Local AI Gateway",
      "options": {
        "baseURL": "http://127.0.0.1:8787/v1",
        "apiKey": "local-only"
      },
      "models": {
        "work-sonnet": {
          "name": "Work Sonnet"
        },
        "personal-gpt": {
          "name": "Personal GPT"
        }
      }
    }
  }
}
```

如果设置了 `GATEWAY_API_KEY`，将 `options.apiKey` 改为相同值。修改 OpenCode 配置后需要重启 OpenCode。

## 工作方式

收到以下请求：

```json
{"model": "work-sonnet", "messages": []}
```

网关查找 `work-sonnet` 路由，将 `model` 替换成配置的上游模型名，把请求发送到该路由的 Base URL，并用该路由自己的 API Key 覆盖 `Authorization` 请求头。上游状态码、响应头和响应体会返回给 Agent。

## 测试

```powershell
.venv\Scripts\python -m pytest
```

运行单个测试：

```powershell
.venv\Scripts\python -m pytest tests/test_gateway.py::test_proxy_rewrites_model_and_authorization
```

## 安全边界

API Key 当前以明文保存在本机 SQLite 数据库中，这是为了保持依赖和部署足够轻量。请限制 `data/gateway.db` 的文件权限，不要提交或同步该文件。管理 API 不带登录机制，只适用于回环地址上的单用户环境。
