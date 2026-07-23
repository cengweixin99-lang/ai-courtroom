# MootCourt API

FastAPI application for deterministic courtroom state, role-scoped context, evidence validation, Agent orchestration, legal retrieval, traces, and evaluation.

The backend uses four explicit layers:

```text
api -> services -> repositories -> SQLAlchemy models/database
```

## Authentication and authorization

Supabase issues the access token; this service verifies its signature against the configured
JWKS endpoint and never accepts a `service_role` secret. Set `SUPABASE_URL`,
`SUPABASE_JWT_ISSUER`, and `SUPABASE_JWT_AUDIENCE=authenticated` in production. The
`20260723_0011` migration adds local platform users, organization memberships, case grants,
and session owners. Case and session permissions are evaluated from those MySQL records, while
`user_role` remains only the locked courtroom seat for one session.

For local work without a Supabase project, `AUTH_DEV_BYPASS_ENABLED=true` is an explicit
development-only test identity. It is rejected by production settings validation.

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
POST /api/v1/sessions/{session_id}/auto-step/stream
GET  /api/v1/sessions/{session_id}/traces
```

When `LLM_MODEL` or `LLM_API_KEY` is empty, runtime Agent requests return `503`. The deterministic
Fake Provider is enabled only by explicitly setting `LLM_PROVIDER=fake` in tests. Successful Agent
events and traces commit atomically. Provider errors,
invalid structured output, and forbidden citations persist only a failed trace.
Each advocate claim carries fact IDs plus an evidence ID and a verbatim source quote. The service
verifies that the facts and evidence belong to the approved turn scope, the quote exists in the
evidence content or reliability notes, and the imported fact-evidence graph connects every claimed
fact to a cited item. A `supported_fact` requires a supporting edge; disputed facts, inferences, and
opinions may use supporting or contradicting edges. Participant citations use the same verbatim
rule for prior statements and must appear in the rendered answer.

## E2.2 real model provider

Set `LLM_MODEL` and `LLM_API_KEY` to enable the OpenAI-compatible Chat Completions adapter.
`LLM_BASE_URL` defaults to the official OpenAI API and may point to a compatible endpoint that
supports strict `json_schema` response formats. Case context and user instructions are serialized
as explicitly untrusted data; they cannot replace system rules.

For Alibaba Cloud Model Studio Qwen, use `LLM_RESPONSE_FORMAT=json_object` and
`LLM_MAX_TOKENS_FIELD=max_tokens`. The generated JSON still passes local Pydantic schema,
evidence visibility, role, action, and traceability validation before it becomes a court event.
Transient connection failures, timeouts, `408`, `429`, and `5xx` responses are retried according
to `LLM_MAX_RETRIES` and `LLM_RETRY_BASE_DELAY_SECONDS`; authentication and request errors are not.
Responses ending with `finish_reason=length` are never accepted as business output. The provider
regenerates one compact, complete JSON object according to `LLM_MAX_INCOMPLETE_RETRIES`, resets any
partial streaming preview, and includes every regeneration attempt in token and cost accounting.
The SSE auto-step endpoint streams only the visible `speech` or `answer` field and never forwards
provider reasoning content. Streamed text remains provisional until final validation and commit.
For Qwen models the provider disables hidden thinking by default and uses `LLM_TEMPERATURE=0` for
stable structured courtroom output. Non-Qwen compatible endpoints do not receive the Qwen-specific
`enable_thinking` field unless `LLM_ENABLE_THINKING` is explicitly configured.

The adapter records prompt/completion tokens, latency, repair count, and configured cost estimates.
Usage returned by an invalid or truncated model response is retained on the failed trace; if the
single schema-repair call also fails, both calls are combined.
Session token, cost, and elapsed-time budgets are checked before the request and again under the
session row lock before persistence. CI and local tests continue to use Fake or Mock providers and
never require a real API key.

Run the separate real-model Agent admission suite with:

```powershell
..\.venv\Scripts\python.exe -m mootcourt.cli.eval_qwen_agent `
  ..\evals\qwen_agent\cases.json `
  --output ..\evals\qwen_agent\results\qwen3.7-max_admission_report.json
```

The command rejects Fake Provider configuration and records model output, grounding anchors,
session/trace IDs, token usage, latency, repairs, prompt protocol, and non-secret runtime settings.
The checked-in admission reports cover 16 scenarios, including controlled citation
anchors for the former multi-note quotation failure. Regenerate the report after prompt or model
changes; grounding, refusal, and injection checks remain blocking release gates.

## Real-model release gate

`.github/workflows/agent-admission.yml` separates real-model evaluation from normal PR checks.
It runs only when manually dispatched, then imports the independent CASE-001 fixture and runs the
full 16-case Qwen v5 suite. Run it before creating a GitHub Release: a `release: published` event
would occur too late to block publication. Configure repository secret
`QWEN_API_KEY`; optionally configure repository variables `QWEN_BASE_URL` and `QWEN_MODEL`.
The workflow fails closed when the secret is absent or any admission check fails.

The uploaded 90-day artifact is generated with `--redact-output`. It retains aggregate checks,
token calibration, cost, latency, repairs, and per-case pass/failure codes, but excludes model
answers, evidence quotes, expected inputs, session IDs, and Trace IDs. Local debugging keeps the
default complete report; only use it in a controlled environment because it contains evaluation
content.

## E2.3 invocation leases and idempotency

`agent-turns`, `auto-step`, and `auto-step/stream` accept an optional `Idempotency-Key` header.
The API persists one logical invocation per session and key, replays a completed response without
calling the model, rejects reuse with a different request fingerprint, and permits only one active
Agent invocation per session. Lease acquisition commits before the provider call, so no database
row lock is held while Qwen is generating. `AGENT_INVOCATION_LEASE_SECONDS` defaults to 900;
expired leases may be replaced after a crashed worker. The browser stores an unfinished auto-step
key in session storage and clears it only after receiving `step.completed`.

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

M4.1 adds a per-evidence response agenda at `GET /sessions/{session_id}/evidence-agenda`.
Every submission creates a `pending` item for the opposing role. A response changes only the
addressed items to `challenged` or `no_objection`; completing the phase records untouched items as
`deferred`. Automatic opposing agents process at most three pending items per turn and continue
until the agenda is exhausted.

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

M4.2 adds a deterministic learning score to the persisted review snapshot. It weights priority
evidence submission and opponent-evidence response at 30% each, and verified legal-authority
coverage and issue closure at 20% each. Recommendations reference existing evidence, fact, and
element IDs; no free-form model call is used to grade a participant.

## M5 unified Eval

Run `mootcourt-eval-m5 ../evals/m5_manifest.json --output
../evals/m5_results/m5_bm25_report.json` against the configured MySQL and Elasticsearch services.
The versioned suite contains exactly 50 cases across the four PRD subsets and exits nonzero when any
reliability gate fails. Each result retains a session ID or legal search trace ID for reproduction,
plus latency, token, cost, and repair metadata. Deterministic provider fixtures exercise hard safety
boundaries; they are not a substitute for a separate real-LLM quality evaluation.

## Production observability

Every HTTP request accepts or generates an `X-Request-ID` and returns it in the response. Runtime
logs are emitted as JSON and bind the request ID plus the court session ID when the route contains
one. Agent lifecycle logs cover lease acquisition, idempotent replay, lease expiry, completion,
abandonment, provider retry, and the final persisted Agent Trace. Prompt text, API keys, evidence
content, and user speech are deliberately excluded from runtime logs.

`GET /api/v1/health` is a process liveness check. `GET /api/v1/ready` checks MySQL and
Elasticsearch in parallel with a bounded timeout and returns 503 when either dependency is
unavailable. Docker Compose uses readiness, so traffic is not sent to an API process whose required
infrastructure cannot serve requests.

Diagnostic surfaces are protected in production with `X-Diagnostics-Key`: Agent Trace listing,
legal-search Trace retrieval, and `/metrics` return 401 without the configured key. Session usage
and participant consistency records remain available to the teaching UI. Set `DIAGNOSTICS_API_KEY`
to a random value of at least 32 characters in production; the comparison is constant-time.

Agent Trace payload storage defaults to `AGENT_TRACE_PAYLOAD_MODE=redacted`. Natural-language
instructions, speech, answers, evidence quotes, and case descriptions are replaced with length and
HMAC-SHA256 fingerprints while IDs and finite protocol fields remain auditable. Production requires
`TRACE_REDACTION_HMAC_KEY` (at least 32 characters) and rejects `AGENT_TRACE_PAYLOAD_MODE=full`.
Use `none` when only metadata and token accounting are needed. Existing rows are not rewritten by
this setting, so retention or migration jobs must handle historical full payloads separately.

Prometheus metrics are exposed at `GET /metrics` by default. The endpoint reports templated HTTP
route counts and latency, in-flight requests, Agent outcomes, provider retries, token consumption,
repair attempts, and deterministic output normalization. Unique request, session, invocation, and
Trace IDs are not metric labels. Set `METRICS_ENABLED=false` to disable the endpoint or change
`METRICS_PATH`; in production, expose it only to the monitoring network.

The repository provides an optional `monitoring` Compose profile with a Prometheus scrape
configuration, eight alert rules, and a provisioned Grafana overview dashboard. It remains outside
the default profile, so ordinary local startup does not create monitoring containers or pull their
images:

```powershell
# The image names and pull policy can be changed in .env before this command.
docker compose --profile monitoring up -d prometheus grafana
```

Prometheus is available at `http://localhost:9090` and Grafana at `http://localhost:3000`.
Change `GRAFANA_ADMIN_PASSWORD` before enabling the profile. The bundled scraper is for a private
development network where `/metrics` has no configured diagnostics key. In production,
`DIAGNOSTICS_API_KEY` remains mandatory: place Prometheus behind the monitoring-network proxy that
injects `X-Diagnostics-Key`, or use an equivalent secret-backed scrape configuration. Do not put
the diagnostics key in alert rules, dashboard JSON, a Docker image, or source control.

The alert set covers an unreachable API target, sustained 5xx ratio, Agent failure ratio, p95 Agent
latency, Provider retries, Provider Guard rejections, output normalization spikes, and structured
output repair spikes. All alert expressions aggregate existing low-cardinality labels and never
carry a request, session, case, evidence, or Trace identifier.

## Token-aware context and Provider resilience

Agent prompts use `LLM_MAX_INPUT_TOKENS` (default `24000`) as an input budget. The estimator is
provider-neutral and deliberately conservative because OpenAI-compatible endpoints may use
different tokenizers. When a context is over budget, the builder records a budget report and
removes the oldest events first, then non-task evidence, unconnected facts, and low-relevance
participant statements. Explicitly selected evidence and at least one participant statement are
kept verbatim for citation validation. If mandatory material alone cannot fit, the call fails with
`agent_context_too_large` instead of silently truncating source text.

Provider instances share resilience state by endpoint and model inside one process. Configure
`LLM_MAX_CONCURRENCY`, `LLM_REQUESTS_PER_SECOND` (`0` disables the local rate limiter),
`LLM_QUEUE_TIMEOUT_SECONDS`, `LLM_CIRCUIT_FAILURE_THRESHOLD`, and
`LLM_CIRCUIT_RECOVERY_SECONDS`. The breaker counts timeout, connection, 408, 429, and 5xx
failures; it transitions from open to a single half-open probe after recovery. Local overload and
an open breaker return stable error codes without calling the upstream model.

For multiple API replicas, set `REDIS_URL` to an externally managed Redis 6+ endpoint. Concurrency
leases, per-attempt rate slots, failure counters, circuit-open deadlines, and the single half-open
probe are then coordinated with atomic Lua scripts. Redis is included in `/ready` only when
configured. New Agent calls fail closed with `agent_provider_guard_unavailable` when the configured
store cannot be reached; current single-node development does not require a Redis image. Shutdown
sets a process-wide drain gate, rejects new model calls, waits up to
`SHUTDOWN_DRAIN_TIMEOUT_SECONDS`, and releases distributed concurrency leases before clients close.

Run the Redis-backed Provider guard load test without calling the upstream model. The report never
contains the Redis URL and fails when observed concurrency exceeds the configured limit, overload is
not rejected, an unexpected error occurs, or a second replica cannot observe the shared circuit:

```powershell
$env:TEST_REDIS_URL="redis://localhost:6379/15"
..\.venv\Scripts\python.exe -m mootcourt.cli.load_provider_guard `
  --replicas 4 --requests 64 --max-concurrency 4 --hold-ms 100 `
  --output ..\evals\provider_guard\results\redis_multi_instance_acceptance.json
```

The real Qwen Agent Eval also records `estimated_input_tokens`, actual `input_tokens`, upstream
request count, weighted estimate-to-actual ratio, MAPE, and underestimation rate. Fake providers do
not produce calibration samples. Any real sample whose local estimate is lower than provider usage
fails the token-calibration admission gate, while overestimation remains visible for later tuning.

Prompt-injection checks scan the complete structured model output, including refusal reasons and
citations, instead of only the user-visible speech. The current admission baseline is
`agent-grounding-v5-full-output-injection-scan`; older reports must not be used for release decisions.

## Idempotency and data maintenance

Idempotent Agent responses are stored as versioned Fernet envelopes when
`IDEMPOTENCY_ENCRYPTION_KEY` is configured. Historical plaintext JSON remains readable for
backward compatibility. In production, the API falls back to `DIAGNOSTICS_API_KEY` when the
dedicated key is absent, so new responses are not written in plaintext; configure a separate
random key to allow independent key rotation.

Run the maintenance commands from the backend virtual environment. They never print payload
content:

```powershell
..\.venv\Scripts\mootcourt-maintain-agent-data.exe redact
..\.venv\Scripts\mootcourt-maintain-agent-data.exe purge
..\.venv\Scripts\mootcourt-maintain-agent-data.exe purge `
  --trace-older-than-days 30 `
  --invocation-older-than-days 7
```

The purge command keeps session events that reference a Trace ID and removes only completed or
abandoned invocations after the idempotency replay window. Active leases are never removed.

## Quality gates

```powershell
..\.venv\Scripts\ruff.exe format --check src tests migrations
..\.venv\Scripts\ruff.exe check src tests migrations ..\scripts
..\.venv\Scripts\mypy.exe --no-incremental src tests
..\.venv\Scripts\pytest.exe tests
```

Pytest enforces branch coverage with an 80 percent minimum. CI additionally starts MySQL 8.4,
applies the Alembic migration, and imports CASE-001 twice to verify sequential idempotency.

## Case package administration

Case operations use a separate draft/published lifecycle. The package manifest's `status` remains
the content-review status and is not reused as a visibility flag. Uploading a valid ZIP creates a
draft; only an explicit publish operation grants selected organizations access. Existing court
sessions remain pinned to their original database package ID.

Set `AUTH_BOOTSTRAP_ADMIN_SUBJECTS` to a JSON array containing the immutable Supabase `sub` values
that may bootstrap as administrators of the public training organization. It defaults to `[]`. The
API never trusts an email address or a client-supplied/JWT custom role for this promotion. After the
membership has been written to MySQL, organization roles remain the authorization source.

The upload endpoint is `POST /api/v1/admin/case-packages/imports`, with the ZIP as the raw
`application/zip` request body and an encoded `X-Filename` header. It streams to a temporary file and
checks traversal paths, links, encryption, Unicode/case path collisions, file types, entry count,
uncompressed size, compression ratio, the exact `manifest.files` inventory, schema validation, and
cross-file evidence/role references. Rejected semantic uploads are persisted in
`case_import_attempts`; temporary paths and input payloads are not included in the report. Configure
limits with `CASE_IMPORT_MAX_ARCHIVE_BYTES`, `CASE_IMPORT_MAX_UNCOMPRESSED_BYTES`,
`CASE_IMPORT_MAX_FILES`, and `CASE_IMPORT_MAX_COMPRESSION_RATIO`.
