# MootCourt API

FastAPI application for deterministic courtroom state, role-scoped context, evidence validation, Agent orchestration, legal retrieval, traces, and evaluation.

The backend uses four explicit layers:

```text
api -> services -> repositories -> SQLAlchemy models/database
```

`services` contains application rules and never imports SQLAlchemy or accepts an
`AsyncSession`. `repositories` owns queries, row locks, ORM mapping, event sequencing, and
the Unit of Work. API and CLI boundaries own commit/rollback.

## E1 deterministic runtime

Install the exact verified development dependency set, then install the local package without
re-resolving dependencies:

```powershell
..\.venv\Scripts\python.exe -m pip install -r requirements-dev.lock
..\.venv\Scripts\python.exe -m pip install --no-deps -e .
```

Create the database schema, import a case package, and start the API:

```powershell
cd backend
..\.venv\Scripts\alembic.exe upgrade head
..\.venv\Scripts\mootcourt-import-case.exe ..\data\authoring\CASE-001
..\.venv\Scripts\uvicorn.exe mootcourt.main:app --reload
```

The importer accepts any package directory that conforms to the case-package schema. The
`data/authoring/CASE-001` path is a development default, not a runtime constant. Files listed under
`runtime_excluded_files`, including `author_only/ground_truth.json`, are never read by the importer.

Initial runtime endpoints:

```text
GET  /api/v1/cases
GET  /api/v1/cases/{case_id}?role=prosecution
POST /api/v1/sessions
GET  /api/v1/sessions/{session_id}
GET  /api/v1/sessions/{session_id}/events
POST /api/v1/sessions/{session_id}/actions
```

接口用途、请求示例、动作枚举和错误约定见 [API.md](API.md)。服务启动后也可以通过
`http://127.0.0.1:8000/docs` 直接查看和调试 OpenAPI 接口。

Session actions are checked against the deterministic phase/role matrix before evidence or events
are persisted. E1 returns fixed responses and does not invoke an LLM.

## E2.1 controlled agent turn

E2.1 adds strict advocate, witness, and defendant output schemas; role-scoped context builders; a
replaceable Agent Provider protocol; persistent call traces; and these endpoints:

```text
POST /api/v1/sessions/{session_id}/agent-turns
GET  /api/v1/sessions/{session_id}/traces
```

When `LLM_MODEL` is empty, the API uses the deterministic Fake Provider and never sends case data
to an external model. Successful Agent events and traces commit atomically. Provider errors,
invalid structured output, and forbidden citations persist only a failed trace.

## E2.2 real model provider

Set `LLM_MODEL` and `LLM_API_KEY` to enable the OpenAI-compatible Chat Completions adapter.
`LLM_BASE_URL` defaults to the official OpenAI API and may point to a compatible endpoint that
supports strict `json_schema` response formats. Case context and user instructions are serialized
as explicitly untrusted data; they cannot replace system rules.

The adapter records prompt/completion tokens, latency, repair count, and configured cost estimates.
Session token, cost, and elapsed-time budgets are checked before the request and again under the
session row lock before persistence. CI and local tests continue to use Fake or Mock providers and
never require a real API key.

## Quality gates

```powershell
..\.venv\Scripts\ruff.exe format --check src tests migrations
..\.venv\Scripts\ruff.exe check src tests migrations ..\scripts
..\.venv\Scripts\mypy.exe --no-incremental src tests
..\.venv\Scripts\pytest.exe tests
```

Pytest enforces branch coverage with an 80 percent minimum. CI additionally starts MySQL 8.4,
applies the Alembic migration, and imports CASE-001 twice to verify sequential idempotency.
