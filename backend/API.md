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
| `GET` | `/sessions/{session_id}/evidence-statuses` | 获取证据提交状态台账 |
| `GET` | `/sessions/{session_id}/evidence-fact-summary` | 获取证据使用与事实支持关系汇总 |
| `GET` | `/sessions/{session_id}/procedural-requests` | 获取程序请求及处理状态 |
| `POST` | `/sessions/{session_id}/procedural-requests/{request_id}/resolution` | 由教学控制者处理程序请求 |
| `POST` | `/sessions/{session_id}/actions` | 校验并持久化一次庭审动作 |
| `POST` | `/sessions/{session_id}/agent-turns` | 执行一次受状态机约束的角色 Agent 回合 |
| `POST` | `/sessions/{session_id}/auto-step/stream` | 以 SSE 流式执行一个自动庭审步骤 |
| `GET` | `/sessions/{session_id}/traces` | 获取 Agent 调用诊断元数据 |
| `GET` | `/sessions/{session_id}/participant-statement-traces` | 获取参与人回答一致性留痕 |
| `POST` | `/legal/search` | 按案件 LegalProfile 检索候选法律依据 |

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
| `state_no_objection` | 对已提交证据明确无异议 | `evidence_ids` |
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
Idempotency-Key: 由客户端为本次逻辑请求生成的唯一键

{
  "actor_role": "witness",
  "participant_id": "W01",
  "action": "make_statement",
  "instruction": "请说明你亲眼看到的内容"
}
```

Agent 输出必须通过严格 Schema，并接受证据或既有陈述的可追溯性校验。律师的每项事实主张
必须提供 `fact_ids` 和 `evidence_id + quote`。事实与证据必须属于本轮批准范围，`quote` 必须能
在证据正文或可靠性说明中逐字找到，而且每个事实必须在案卷关系图中连接到所引证据；
`supported_fact` 只接受支持证据，争议事实、推断和意见可使用支持或反驳证据。证人和被告
引用既有陈述时同样必须提供 `statement_id + quote`，且原文
片段必须直接出现在回答中。举证动作要求覆盖本轮明确提交的全部证据；质证动作允许 Agent
从本轮批准范围中选择部分证据，不要求对没有发表意见的证据强行生成异议。质证事件只记录
实际被引用的证据。首次格式错误
时最多修复一次。成功调用会把庭审事件和 Trace 放在同一事务中提交；模型异常、修复
失败或输出越权时返回 `502`，只保存失败 Trace，不写入庭审事件。

Trace 查询接口只返回调用状态、Provider、模型、Token、延迟、成本和错误元数据，
不会通过 API 暴露完整上下文快照。未配置 `LLM_MODEL` 或 `LLM_API_KEY` 时返回 `503`；
只有显式设置 `LLM_PROVIDER=fake` 才使用确定性的 Fake Provider。

前端自动流程使用 `/auto-step/stream`，按顺序接收 `step.started`、`turn.started`、
`turn.delta`、`turn.validating` 和 `step.completed`。`turn.delta` 是尚未校验的完整文本快照，
仅用于临时展示；只有 `step.completed` 返回的事件才是已通过 Schema、权限校验并提交的
正式庭审记录。连接断开时客户端可以重试当前步骤，不会重复提交已经成功的事件。
`agent-turns`、`auto-step` 和 `auto-step/stream` 均接受 `Idempotency-Key` 请求头。客户端在
断线重连时复用原键，开始下一个逻辑步骤时必须更换键。已完成请求返回原结果并设置
`Idempotency-Replayed: true`；同一键绑定不同请求返回 `409 idempotency_key_reused`，同一
会话已有其他调用则返回 `409 agent_invocation_in_progress`。租约只在短数据库事务中争抢，
模型生成期间不会持有会话行锁。

配置真实 OpenAI-compatible Provider：

```env
LLM_PROVIDER=openai-compatible
LLM_MODEL=qwen3.7-max
LLM_API_KEY=仅保存在本地环境变量或密钥服务中
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_TIMEOUT_SECONDS=60
LLM_MAX_OUTPUT_TOKENS=3000
LLM_MAX_RETRIES=2
LLM_MAX_INCOMPLETE_RETRIES=1
LLM_RETRY_BASE_DELAY_SECONDS=0.5
LLM_RESPONSE_FORMAT=json_object
LLM_MAX_TOKENS_FIELD=max_tokens
LLM_ENABLE_THINKING=false
LLM_TEMPERATURE=0
LLM_INPUT_COST_PER_MILLION_CNY=0
LLM_OUTPUT_COST_PER_MILLION_CNY=0
AGENT_INVOCATION_LEASE_SECONDS=900
```

其他兼容服务可使用 `LLM_RESPONSE_FORMAT=json_schema` 和
`LLM_MAX_TOKENS_FIELD=max_completion_tokens`。使用百炼新加坡地域时，将 Base URL 替换为
`https://dashscope-intl.aliyuncs.com/compatible-mode/v1`。
系统不会猜测模型价格；两个成本参数必须按实际部署价格填写，为 `0` 时仍记录 Token，
但成本估算为 `0`。每次调用前后都会检查会话 Token、成本和时间预算，超限返回 `429`
并只保存失败 Trace。Provider 未配置返回 `503`，上游超时、拒绝或无效响应返回 `502`。
模型已经返回 usage 但内容无效或被截断时，该消耗仍写入失败 Trace 并计入后续会话预算；
格式修复调用失败时会合并首次调用和修复调用的 usage。
连接中断、超时、`408`、`429` 和 `5xx` 默认最多重试两次并采用指数退避；鉴权和参数
错误不重试。重试期间不会重复写入庭审事件。
Qwen3/3.7 的结构化庭审任务默认关闭隐藏 thinking，并使用 temperature 0；这两个值会随
真实 Agent Eval 报告冻结。首次 Schema 或确定性业务落地校验失败时只允许一次修复，修复
调用的 Token、成本和延迟会合并进入同一 Trace。

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
状态冲突，`422` 表示请求结构或动作参数不合法，`429` 表示会话预算耗尽，`502` 表示
模型调用或输出校验失败且失败 Trace 已保存，`503` 表示真实 Provider 未正确配置。

## 法律检索

首次检索前执行幂等索引命令：

```powershell
cd backend
..\.venv\Scripts\mootcourt-index-legal.exe ..\knowledge\legal\source_manifest.json
```

索引器只接受 `source_manifest.json` 中显式批准且审核状态允许的条款。客户端不能指定
法域、生效日期或来源范围；这些过滤条件来自案件锁定的 `LegalProfile`。

```http
POST /api/v1/legal/search
Content-Type: application/json

{
  "case_id": "CASE-001",
  "query": "盗窃罪第二百六十四条的入罪条件",
  "top_k": 5
}
```

接口返回条款原文、条款号、效力日期、审核状态、官方来源、版本哈希和检索分数。
默认 `retrieval_mode=bm25`；显式配置法律 embedding 后返回 `hybrid_rrf`，并同时保留
BM25、向量原始分及各自排名。RRF 分数仅用于候选排序，不能解释为法律结论置信度。
索引正常但没有可靠召回时返回 `INSUFFICIENT_LEGAL_AUTHORITY`；索引不存在或
Elasticsearch 不可用时返回 `503 legal_search_unavailable`，系统不得改用模型记忆补写。
本接口只提供候选依据，不生成构成要件判断或法律结论。

成功响应包含 `trace_id`。可用该标识读取固定过滤条件、候选快照、BM25/向量/RRF 分数、
embedding 版本和耗时：

```http
GET /api/v1/legal/search-traces/{trace_id}
```

模型或后续业务模块引用法条前，必须提交引用校验：

```http
POST /api/v1/legal/citations/validate
Content-Type: application/json

{
  "trace_id": "检索响应中的 trace_id",
  "citations": [
    {
      "source_id": "LS-CPL-51",
      "article_number": "第五十一条",
      "text": "必须与 Trace 中完整原文逐字一致",
      "official_source_url": "https://flk.npc.gov.cn/",
      "version_hash": null
    }
  ]
}
```

校验只认可该 Trace 实际召回的候选，并严格比较条款号、完整原文、官方来源 URL 和版本
哈希。伪造 source ID、历史版本替换或任何字段篡改都会返回 `valid=false` 及具体失败类型。

## M4 证据状态和程序请求

`GET /api/v1/sessions/{session_id}/evidence-statuses` 返回当前角色的证据可用性、
`not_submitted` / `submitted` 状态、提交角色和时间，不暴露无权访问的证据正文。

`GET /api/v1/sessions/{session_id}/evidence-agenda` 返回逐证据回应议程。状态包括
`pending`、`challenged`、`no_objection` 和 `deferred`。质证或无异议只能处理当前席位的
`pending` 项；在仍有待回应项时完成阶段，会将这些项目确定性记录为 `deferred`。

证据质证必须选择 `AUTHENTICITY`、`LEGALITY`、`RELEVANCE` 或 `PROBATIVE_VALUE`
中的至少一项。问题制止请求使用 `IRRELEVANT_QUESTION`、`REPETITIVE_QUESTION` 或
`IMPROPER_QUESTION`，并通过 `target_event_sequence` 指向已发生的发问事件。

```json
{
  "action": "challenge_evidence",
  "evidence_ids": ["E03"],
  "challenge_dimensions": ["AUTHENTICITY", "PROBATIVE_VALUE"],
  "content": "门禁卡被使用不能单独证明登记人本人进入。"
}
```

```json
{
  "action": "state_no_objection",
  "evidence_ids": ["E01", "E02"]
}
```

```json
{
  "action": "raise_procedural_request",
  "procedural_request_type": "REPETITIVE_QUESTION",
  "target_event_sequence": 12,
  "content": "该问题此前已经提出，请求制止。"
}
```

`GET /api/v1/sessions/{session_id}/procedural-requests` 返回结构化质证与问题制止记录。
重复问题只做空白和末尾标点归一化后的确定性比较；无关或不当问题不由模型自动裁定，
状态为 `pending_controller_review`。证据质证状态为 `recorded_for_evaluation`。

问题制止请求由教学流程控制者通过以下接口处理，结果使用中国刑事教学语境下的
`APPROVED`（准许）、`REJECTED`（驳回）；证据质证只能使用 `RECORDED`（记入评议）。
处理结果与公开庭审事件在同一事务写入，已经处理的请求不能重复处理。当前版本尚未接入
真实身份认证，因此该接口只能部署在受信任的教学控制端之后。

```http
POST /api/v1/sessions/SESSION_ID/procedural-requests/REQUEST_ID/resolution
Content-Type: application/json

{
  "resolution": "APPROVED",
  "reason": "问题含有预设事实，应当调整问法。"
}
```

证人或被告人的成功 Agent 回合会同步写入 `participant-statement-traces`。记录包含回答、
引用的案卷陈述、由陈述关联得到的事实和确定性一致性分类；系统不会以字符串相似度推断
语义矛盾。`evidence-fact-summary` 汇总每项事实的关联证据、已提交证据和庭审中已出现的
陈述。其 `support_status` 仅表示材料使用进度，不表示法院已经认定该事实成立。

被告人输出 `new_statement=true` 时允许写入公开庭审记录，但一致性状态固定为
`NEW_STATEMENT_PENDING_REVIEW`。教学控制者必须通过以下接口决定是否纳入本庭陈述记录：

```http
POST /api/v1/sessions/SESSION_ID/participant-statement-traces/TRACE_ID/resolution
Content-Type: application/json

{
  "resolution": "INCLUDED_IN_RECORD",
  "reason": "作为本庭新增陈述保留，但不自动认定相关事实。"
}
```

可选值为 `INCLUDED_IN_RECORD` 和 `EXCLUDED_FROM_RECORD`。无论选择哪一项，系统都不会
为新增陈述自动补写事实 ID，也不会将其直接视为已证实事实。

## M5.0 结构化教学复盘

进入 `LEGAL_ANALYSIS` 后，客户端提交覆盖全部冻结构成要件法源的本案检索 Trace：

```http
POST /api/v1/sessions/SESSION_ID/review
Content-Type: application/json

{
  "legal_search_trace_ids": ["TRACE_ID_1", "TRACE_ID_2"]
}
```

生成前必须处理全部程序请求和本庭新增陈述。Service 会确认每个 Trace 属于当前案件版本，
并逐字段核对条款号、完整原文、官方来源和版本哈希。任一构成要件缺少所需法源时返回
`insufficient_legal_authority`，不会由模型记忆补写。

报告按 `SUPPORTED`、`DISPUTED`、`INSUFFICIENT` 形成逐项教学模拟事实判断，再聚合为
六个冻结构成要件的状态并附引用。CASE-001 未获现实法律结论准入，因此
`deterministic_conclusion_allowed=false` 且 `conclusion=null`。已生成报告可通过
`GET /api/v1/sessions/{session_id}/review` 读取持久化快照。

## M4.2 确定性教学评分

结构化复盘同时返回 `total_score`、`score_dimensions` 和 `recommendations`。评分不调用
LLM，而是从已持久化的证据提交、逐证据议程、法源 Trace、事实判断和构成要件状态计算：

- `priority_evidence_submission`：本席位优先证据提交覆盖，权重 30%；
- `opponent_evidence_response`：质证或明确无异议的对方证据覆盖，权重 30%；
- `legal_authority_coverage`：冻结构成要件所需法源覆盖，权重 20%；
- `issue_closure`：形成明确判断的构成要件比例，权重 20%，争议状态按半分计入。

没有配置优先证据或没有对方证据需要回应时，该维度记为不适用并按 100 分处理，不会
人为扣分。建议只引用当前会话已有的证据、事实和构成要件 ID，可定位未提交的优先证据、
被暂缓的质证项以及仍未闭合的事实或要件。评分随复盘报告持久化，后续读取不会因案卷
或评分规则调整而改变既有教学记录。

## M4.3 逐发言诊断与深度点评

复盘中的 `turn_diagnostics` 只使用用户发言事件的结构化字段，检查证据锚点、关联事实、
质证维度和询问对象。前端可按 `event_sequence_number` 返回并高亮对应庭审记录。

`POST /api/v1/sessions/{session_id}/review/turn-evaluation` 使用真实 OpenAI-compatible Qwen
生成表达组织、回应质量和攻防策略点评；结果独立保存，不参与确定性总分，也不能修改庭审
事件或法律结论。模型返回的事件、证据和事实 ID 必须属于本次允许范围，否则整份点评拒绝
落库。已生成结果通过同路径 `GET` 读取。
