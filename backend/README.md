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

## M3.1 legal source indexing and BM25

Index the approved CASE-001 legal baseline into Elasticsearch 8.19.10:

```powershell
..\.venv\Scripts\mootcourt-index-legal.exe ..\knowledge\legal\source_manifest.json
```

The command is idempotent and uses stable `source_id` document IDs. Runtime search applies the
case LegalProfile source allowlist, normalized jurisdiction, law-as-of date, effective status, and
approved review status before BM25 ranking. The historical 2009 Criminal Law record remains in the
index for version-filter evaluation but cannot be returned for CASE-001 at the 2026 baseline.

The current workspace is missing the declared official Criminal Law PDF snapshot. This is recorded
as a release blocker and does not authorize legal conclusions; M3.1 only supports development
retrieval over reviewed article text snapshots.

## M3.2 legal retrieval evaluation

Run the reviewed 20-case BM25 baseline after importing CASE-001 and indexing legal sources:

```powershell
..\.venv\Scripts\mootcourt-eval-legal.exe `
  ..\evals\legal_rag\bm25_baseline_cases.json `
  --output ..\evals\legal_rag\results\bm25_baseline_report.json
```

The command uses the complete application retrieval path, including the case LegalProfile stored in
MySQL. It reports macro Recall@5, Precision@5, mean reciprocal rank, validity-filter accuracy, and
refusal accuracy. A failed PRD threshold produces exit code 1. When running the CLI on the Windows
host, set `DATABASE_URL` to the mapped MySQL endpoint `127.0.0.1:3307`; containers continue to use
`mysql:3306`.

## M3.3 hybrid legal retrieval

Hybrid retrieval is opt-in. Configure a reviewed Chinese legal embedding model through the
`LEGAL_EMBEDDING_*` settings, set a stable non-`disabled` version, then rerun
`mootcourt-index-legal`. The indexer embeds the instrument title, article number, and complete
article text; it never splits a statutory article by fixed character length.

At query time the service executes BM25 and Elasticsearch kNN with the same LegalProfile source
allowlist, jurisdiction, effective-date, status, and review filters. Application-level reciprocal
rank fusion returns one candidate list while preserving each branch's raw score and rank. Vector
hits below `LEGAL_VECTOR_SIMILARITY_THRESHOLD` are discarded, and documents created by another
embedding version cannot enter the vector branch.

For Elasticsearch cosine kNN, `LEGAL_VECTOR_SIMILARITY_THRESHOLD` is the raw cosine threshold;
the returned `_score` is `(1 + cosine) / 2`. The frozen CASE-001 value is `0.78`, selected on the
reviewed Eval set to preserve BM25 precision while improving reciprocal rank.

Run the same Eval command with embeddings enabled and write a separate report, for example
`evals/legal_rag/results/hybrid_rrf_report.json`. Do not overwrite the committed BM25 baseline.
Without a real reviewed embedding model, tests can validate the vector pipeline but cannot claim a
semantic retrieval improvement.

### Embedding admission workflow

`knowledge/legal/embedding_models.json` is the model registry. The first engineering candidate is
Ollama `bge-m3`, frozen as model ID `790764642607`, 1024 dimensions, and
`ollama-bge-m3-790764642607-1024-v1`. Its automated Eval passed, but the status remains
`automated_eval_passed_pending_human_review`; the API refuses to enable it until the profile is
explicitly approved for runtime.

After deploying an OpenAI-compatible embedding endpoint, configure matching
`LEGAL_EMBEDDING_*` values and run:

```powershell
..\.venv\Scripts\mootcourt-index-legal.exe ..\knowledge\legal\source_manifest.json
..\.venv\Scripts\mootcourt-eval-legal.exe `
  ..\evals\legal_rag\bm25_baseline_cases.json `
  --output ..\evals\legal_rag\results\hybrid_rrf_report.json
..\.venv\Scripts\mootcourt-compare-legal-evals.exe `
  ..\evals\legal_rag\results\bm25_baseline_report.json `
  ..\evals\legal_rag\results\hybrid_rrf_report.json `
  --policy ..\evals\legal_rag\hybrid_admission_policy.json
```

The comparison command returns exit code 1 when dataset identity, Recall@5, Precision@5, MRR,
validity filtering, or refusal safety fails the versioned policy. Passing the automated policy does
not mutate the registry; review the per-case traces before changing `enabled_for_runtime`.

On the current Windows development machine, Ollama 0.32.1 serves the frozen `bge-m3` model at
`http://127.0.0.1:11434/v1`. Run `scripts/eval_legal_hybrid.ps1` from the repository root to verify
the exact model ID, rebuild vectors, rerun the 20-case Eval, and regenerate the comparison report.

## M3.4 legal citation audit

Every successful legal search now persists a `legal_search_traces` row in the same database
transaction and returns its `trace_id`. The trace freezes the case package, LegalProfile, mandatory
filters, retrieval mode, embedding version, candidate snapshots, scores, ranks, and latency. It does
not store vectors, API keys, or database credentials.

`GET /api/v1/legal/search-traces/{trace_id}` exposes the auditable snapshot. Before a candidate is
used as a direct legal citation, `POST /api/v1/legal/citations/validate` requires the source ID,
article number, complete text, official URL, and version hash to match that exact trace. A source
outside the retrieved set or any modified field is rejected with a structured reason.

## M4 evidence and procedural requests

The session action service persists structured evidence challenges and question-control requests.
Evidence challenges require submitted, role-visible evidence plus one or more authenticity,
legality, relevance, or probative-value dimensions. Irrelevant, repetitive, and improper-question
requests must target a prior question event; exact repeats are checked deterministically after
whitespace and terminal-punctuation normalization.

Evidence status and procedural request audit views are available under each session. Question
control remains pending controller review, while evidence challenges are recorded for later
evaluation. Neither path invokes an LLM to simulate a procedural ruling.

M4 controller resolution uses `APPROVED` or `REJECTED` for question-control requests and
`RECORDED` for evidence challenges. Resolution and its public courtroom event are persisted in one
transaction and cannot be repeated. This is currently a trusted teaching-controller endpoint; real
authentication is not implemented yet.

Successful witness and defendant turns persist participant statement traces with cited statement
IDs, related fact IDs, and deterministic consistency classifications. The evidence-fact summary
reports submitted support and appeared statements for each fact, but deliberately does not make a
judicial fact finding.

## M5.0 statement review and structured court review

Defendant outputs marked as new in-court statements are persisted as pending controller review.
The controller may include or exclude the statement from the record, but neither decision creates
fact links or treats the statement as proven.

During `LEGAL_ANALYSIS`, the structured review service consumes only public session records,
submitted evidence, resolved procedural requests, frozen legal elements, and search traces belonging
to the locked case version. Every required citation must exactly match the imported article text and
version metadata. Missing authority stops report generation. CASE-001 remains a development teaching
simulation, so the report exposes fact and element statuses but never emits a real legal conclusion.

## M5 unified Eval

Run `mootcourt-eval-m5 ../evals/m5_manifest.json --output
../evals/m5_results/m5_bm25_report.json` against the configured MySQL and Elasticsearch services.
The versioned suite contains exactly 50 cases across the four PRD subsets and exits nonzero when any
reliability gate fails. Each result retains a session ID or legal search trace ID for reproduction,
plus latency, token, cost, and repair metadata. Deterministic provider fixtures exercise hard safety
boundaries; they are not a substitute for a separate real-LLM quality evaluation.

## Quality gates

```powershell
..\.venv\Scripts\ruff.exe format --check src tests migrations
..\.venv\Scripts\ruff.exe check src tests migrations ..\scripts
..\.venv\Scripts\mypy.exe --no-incremental src tests
..\.venv\Scripts\pytest.exe tests
```

Pytest enforces branch coverage with an 80 percent minimum. CI additionally starts MySQL 8.4,
applies the Alembic migration, and imports CASE-001 twice to verify sequential idempotency.
