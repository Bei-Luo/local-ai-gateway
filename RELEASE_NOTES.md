# v0.1.0

Local AI Gateway 的首个稳定版本，为 OpenCode 等本地 AI Agent 提供轻量级 OpenAI-compatible 模型路由。

## 主要功能

- 按本地模型名将请求转发到不同 OpenAI-compatible 上游。
- 每条线路独立配置站点、上游模型和 API Key。
- 相同本地模型名支持多条线路，启用一条时自动停用其他同名线路。
- 透传 `/v1/responses`、`/v1/chat/completions` 等 JSON 与 SSE 流式响应。
- 从上游 `/models` 发现模型，并使用 Responses 流式请求检测线路可用性。
- 提供本地管理页面、网关 API 令牌和最近 1000 条使用记录。
- SQLite 单文件持久化，无 Node.js 运行时依赖。

## Windows 启动

完成安装后运行：

```powershell
.\scripts\start.ps1
```

自定义端口：

```powershell
.\scripts\start.ps1 -Port 8788
```

脚本固定监听 `127.0.0.1`，并在端口被占用时报告占用进程 PID。

## 数据库迁移

应用启动时自动迁移现有 `data/gateway.db`，保留路由、API Key、网关令牌和使用记录。升级前仍建议停止网关并备份数据库文件。

完整说明见 [MIGRATIONS.md](MIGRATIONS.md)。

## 已知限制

- 上游 API Key 和数据库内生成的网关令牌仍以明文保存。
- 管理接口没有登录，只应监听本机回环地址。
- 尚未提供 PyInstaller 单文件发行版。
