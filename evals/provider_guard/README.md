# Provider Guard 多实例压测

该压测只验证 Redis 分布式并发租约、拒绝码和共享熔断，不会调用真实 LLM，也不会把
Redis URL 写入报告。

```powershell
cd backend
$env:TEST_REDIS_URL="redis://localhost:6379/15"
..\.venv\Scripts\python.exe -m mootcourt.cli.load_provider_guard `
  --replicas 4 `
  --requests 64 `
  --max-concurrency 4 `
  --hold-ms 100 `
  --output ..\evals\provider_guard\results\redis_multi_instance_acceptance.json
```

以下条件必须同时满足：

- 实际最大并发不超过配置值；
- 存在超限请求时必须返回 `agent_provider_overloaded`；
- 不允许出现未分类异常；
- 一个实例触发熔断后，另一个实例必须返回 `agent_provider_circuit_open`。
