# MootCourt HTTP API

所有接口使用 `/api/v1` 前缀。启动服务后可在
`http://127.0.0.1:8000/docs` 查看可交互的 OpenAPI 文档。

## 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/health` | 检查 API 进程是否存活 |
| `GET` | `/cases` | 列出已导入运行库的案件包版本 |
| `GET` | `/cases/{case_id}` | 获取指定角色可见的案件内容 |
| `POST` | `/sessions` | 创建并锁定案件版本和用户角色的庭审会话 |
| `GET` | `/sessions/{session_id}` | 恢复会话当前状态 |
| `GET` | `/sessions/{session_id}/events` | 获取按序号排列的不可变事件日志 |
| `POST` | `/sessions/{session_id}/actions` | 校验并持久化一次庭审动作 |
| `POST` | `/sessions/{session_id}/agent-turns` | 执行一次受状态机约束的角色 Agent 回合 |
| `GET` | `/sessions/{session_id}/traces` | 获取 Agent 调用诊断元数据 |

## 角色隔离

案件详情接口必须提供 `role=prosecution` 或 `role=defense`。返回内容会在
Service 层按角色过滤，不包含其他角色的专属材料、禁止公开的事实或创作阶段的
`ground_truth` 数据。客户端不能通过动作请求指定执行角色；服务端始终使用会话创建时
持久化的角色。

```http
GET /api/v1/cases/CASE-001?role=prosecution&package_version=1.0.0
```

## 创建会话

`package_version` 可省略，此时使用该案件最新导入的版本。实际使用的版本会写入会话，
后续导入新版本不会改变正在进行的庭审。

```http
POST /api/v1/sessions
Content-Type: application/json

{
  "case_id": "CASE-001",
  "user_role": "prosecution",
  "package_version": "1.0.0"
}
```

## 执行庭审动作

当前支持以下 `action` 值：

| 值 | 含义 | 相关字段 |
| --- | --- | --- |
| `advance_phase` | 推进至下一庭审阶段 | 无 |
| `make_statement` | 发表陈述 | `content` |
| `submit_evidence` | 提交证据 | `evidence_ids` |
| `challenge_evidence` | 对已提交证据质证 | `evidence_ids`, `content` |
| `question_participant` | 询问参与人 | `target_id`, `content` |
| `raise_procedural_request` | 提出程序性请求 | `content` |
| `generate_legal_analysis` | 生成法律分析阶段动作 | 无 |
| `view_review` | 查看评审阶段动作 | 无 |

每次请求都会先校验庭审阶段和角色，再校验证据可见性、重复提交、质证前置提交、
参与人存在性和回合上限。校验失败不会写入事件。E1 返回固定反馈，`agent_invoked`
始终为 `false`，不会调用大语言模型。

```http
POST /api/v1/sessions/SESSION_ID/actions
Content-Type: application/json

{
  "action": "submit_evidence",
  "evidence_ids": ["E03"]
}
```

成功响应同时返回最新 `session`、本次持久化的 `event`、`agent_invoked` 和
`fixed_response`。完整字段及枚举以 `/docs` 中的 OpenAPI Schema 为准。

## Agent 回合

`/agent-turns` 只允许调用用户未选择的公诉方或辩护方，以及受系统控制的证人和
被告人。客户端提交的角色、动作和参与人只代表调用意图，服务端会根据会话中的用户
角色、当前阶段、证据权限和参与人类型重新校验。

```http
POST /api/v1/sessions/SESSION_ID/agent-turns
Content-Type: application/json

{
  "actor_role": "witness",
  "participant_id": "W01",
  "action": "make_statement",
  "instruction": "请说明你亲眼看到的内容"
}
```

Agent 输出必须通过严格 Schema，并接受证据或既有陈述的可追溯性校验。首次格式错误
时最多修复一次。成功调用会把庭审事件和 Trace 放在同一事务中提交；模型异常、修复
失败或输出越权时返回 `502`，只保存失败 Trace，不写入庭审事件。

Trace 查询接口只返回调用状态、Provider、模型、Token、延迟、成本和错误元数据，
不会通过 API 暴露完整上下文快照。E2.1 默认在未配置 `LLM_MODEL` 时使用确定性的
Fake Provider，不发起任何外部模型请求。

## 错误格式

业务错误使用 HTTP 状态码和稳定的 `detail.code`。`message` 用于排查和展示，客户端
分支逻辑应依赖 `code`，不要依赖可能调整的文案。

```json
{
  "detail": {
    "code": "action_not_allowed",
    "message": "prosecution cannot submit_evidence during COURT_OPENING"
  }
}
```

常见状态码：`403` 表示角色不可见，`404` 表示资源不存在，`409` 表示与当前会话
状态冲突，`422` 表示请求结构或动作参数不合法，`502` 表示模型调用或输出校验失败且
失败 Trace 已保存。
