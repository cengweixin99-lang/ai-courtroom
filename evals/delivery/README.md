# Docker 交付验收

该 Runner 通过已发布的 HTTP API 验证 Docker 运行态，不在进程内绕过 API 调用 Service。
默认 `smoke` 模式不调用 LLM；`full` 模式会使用当前 OpenAI-compatible 配置完成一场真实
Qwen 庭审，因此会产生 Token 和费用。

从项目根目录运行：

```powershell
.\scripts\accept_delivery.cmd
```

真实模型完整验收：

```powershell
.\scripts\accept_delivery.cmd --full
```

默认检查 API、Web、Elasticsearch、Alembic 版本、CASE-001、角色材料隔离、法律索引和
会话审计起点。完整模式额外检查 SSE、幂等回放、自动编排、用户合法动作、复盘和 Qwen
逐发言点评。报告同时生成 JSON 与同名 Markdown 文件；任一检查失败时命令返回非零退出码。

脚本不会拉取镜像、重建容器、停止服务或删除 MySQL/Elasticsearch 数据卷。每次运行会
创建独立验收会话，并保留 session ID 和法律检索 trace ID 以便审计。
