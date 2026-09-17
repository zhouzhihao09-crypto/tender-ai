# Phase 15 — Production Hardness & Deployment Readiness

## Audit & Implementation Plan

> This document is a production-readiness audit of the Tender AI codebase as it
> stands after Phase 14.  It records the current state, defines a realistic
> production target, categorises findings, and produces a concrete implementation
> plan.  **Nothing is deployed in this phase.**

---

## 0. Baseline verification (current state)

| Check | Result |
|---|---|
| Full test suite (`pytest -q`) | **68 passed**, 0 failed (pre-15A baseline) |
| Phase 10–14 focused tests | **22 passed** |
| SCA regression tests | **3 passed** |
| `frontend/app.js` syntax | **PASS** |
| `frontend/workspace.js` syntax | **PASS** |
| Local uvicorn startup | **PASS** |
| `/health` | HTTP 200 |
| `/ready` | HTTP 200 |
| `SCA_tender.pdf` | unchanged |

No Phase 15 work has been started.  All items below are audit findings against the
current source tree.

---

## 1. Current project inspection

### Stack summary

| Layer | Technology |
|---|---|
| API / web framework | FastAPI 0.115, Uvicorn 0.34 |
| ORM | SQLAlchemy 2.0 |
| Database | SQLite (local dev); PostgreSQL via `psycopg[binary]` (configured in `DATABASE_URL`) |
| Frontend | Server-served static HTML/CSS/JS (no SPA framework, no build step) |
| File storage | `LocalFileStorage` (dev) / `ObjectStorage` S3-compatible via `boto3` (prod) |
| Embeddings | `sentence-transformers` + FAISS (optional); deterministic fallback always available |
| LLM | Ollama local provider; hosted providers via `LLMProvider` protocol stub |
| Billing | Stripe Python SDK (test mode only) |
| Passwords | `hashlib.scrypt` with per-user salt (no external dependency) |
| Sessions | Server-side `UserSession` table; HMAC token hash; HttpOnly/SameSite=Lax cookies |
| Container | `python:3.12-slim` Dockerfile |

### Key service boundaries

* `backend/app/services/analysis_service.py` — `analyze_tender(tender_id, db)` orchestrates PDF extraction → chunking → embedding/indexing → local analysis → (optional) LLM grounded JSON → persistence. Runs synchronously within a FastAPI `BackgroundTasks` invocation.
* `backend/app/services/package_service.py` — `export_package()` builds a ZIP manifest and writes it through the storage adapter.
* `backend/app/services/evidence_service.py` — `store_pdf()` saves and re-extracts evidence documents.
* `backend/app/billing_service.py` — Stripe checkout, portal, subscription lifecycle, webhook verification.

### Configuration (`config.py`)

* `Settings` model loads from `.env` (or environment).  Additional Phase 14 fields:
  `storage_backend`, `s3_bucket`, `s3_endpoint_url`, `s3_region`, `s3_access_key_id`,
  `s3_secret_access_key`, `ai_provider`, `allowed_origins`, `allowed_hosts`, `log_level`.
* **Production fail-safe validation** runs at import time: rejects production
  mode unless `AUTH_REQUIRED=true`, non-placeholder `AUTH_SECRET`, `ALLOWED_ORIGINS`,
  and `ALLOWED_HOSTS` are set.  Also requires `S3_BUCKET` when `STORAGE_BACKEND=s3`.

### Database migration mechanism (`database.py`)

* `initialize_database()` calls `Base.metadata.create_all()` followed by a series of
  `ALTER TABLE ... ADD COLUMN` statements guarded by `inspector.get_columns()`
  checks.  This is a **manual, ad-hoc compatibility layer**, not a versioned
  migration system.  There is no Alembic (no `alembic/` directory, no
  `alembic.ini`, no `alembic` in `requirements.txt`).
* Tables and columns migrated inline: `users.plan`, `users.subscription_status`,
  `users.stripe_customer_id`, `tenders.workspace_id`, `company_evidence_documents.workspace_id`,
  `company_profiles.workspace_id`, `tenders.analysis_started_at`,
  `tenders.analysis_completed_at`, `tenders.summary_json`, `tenders.analysis_json`,
  `tender_sources.chunk_id`, `tender_sources.relevance_score`,
  `tender_sources.retrieval_method`, `tender_requirement_matches.evidence_document_count`,
  `tender_requirement_matches.evidence_summary`, `bid_clarifications.answer`.
* Local dev creates a bootstrap user (`local-dev@example.invalid`) with
    `plan=BUSINESS` and a `local-development` workspace.

### Storage (`storage.py`)

* `DocumentStorage` Protocol with `save`, `read`, `delete`, `exists`, `path`,
  `writable_path`.
* `LocalFileStorage` — filesystem; path-traversal protection via
  `_safe_path()` (rejects `..`, non-basename names, out-of-root resolved paths).
* `ObjectStorage` — S3-compatible; `boto3` imported lazily; same traversal
  protection in `_key()`; `path()` downloads to a `NamedTemporaryFile`; temp files
  are tracked in `self.temp_files` but **never cleaned up** (no `__del__`, no
  background sweep).
* Module-level instances: `document_storage`, `evidence_storage`,
  `export_storage` — all created at import time with `settings.upload_dir` /
  `settings.company_evidence_dir` / `settings.export_dir`.

### AI provider (`services/ai_provider.py`)

* `LLMProvider` Protocol with `generate_grounded_json(evidence_by_category)`.
* `OllamaProvider` — preserves exact prompt/HTTP contract.
* `_HostedProviderStub` — returns `None` for any non-`ollama` provider name.
  `llm_service.py` re-exports `generate_grounded_json` for backward compat.
* `analysis_service.py` line 169: `llm_result = generate_grounded_json(evidence)`
  — if `None`, falls through to `build_local_analysis()` deterministic path.

### Security posture

**Strengths:**
* PDFs validated by header (`%PDF`) and size limit before storage.
* Filenames replaced with UUID-based `stored_file_name`.
* Path-traversal protection in both storage backends.
* `enforce_request_ownership` dependency on the main router checks
  `tender_id`, `document_id`, `evidence_document_id`, `bid_document_id`,
  `item_id`, `action_id` against the authenticated user's owned workspaces.
* Password hashing uses `scrypt` with 16-byte salt.
* Security headers middleware (CSP, nosniff, DENY, Referrer-Policy,
  X-XSS-Protection: 0).
* TrustedHostMiddleware in production.
* Stripe webhook signature verification on raw request body.
* Production fail-safe config validation.

**Gaps (detailed in §8):**
* No rate limiting on auth endpoints.
* No CSRF protection (mitigated by SameSite=Lax, but cross-origin POST is
  not explicitly blocked).
* `development_user()` returns a pre-seeded user when `auth_required=False`
  — this is a dev convenience, not a production risk, but the code path
  should be audited.
* No request body size limit at the ASGI/uvicorn level (only per-endpoint
  checks via `max_upload_size_mb`).
* `UserSession.token_hash` is computed with `auth_secret` — if `auth_secret`
  rotates, all sessions are invalidated (acceptable but should be documented).
* Error messages may leak internal paths or stack-trace-derived details
  in some `except Exception as error: raise HTTPException(400, detail=str(error))`
  patterns (e.g. `upload_company_evidence`).

### Billing / Stripe (`billing_service.py`)

* Checkout: resolves internal plan → `STRIPE_PRICE_*` env var → Stripe
  Checkout Session.  Only `PAID_PLANS` (starter, professional, business)
  have checkout; Free has no checkout button.
* `customer_for_user()` reuses existing `stripe_customer_id` or creates a
  new Stripe Customer (metadata includes `user_id`).
* Webhook: raw body signature verification via `stripe.Webhook.construct_event`.
  Idempotent via `BillingEvent` table.
* Subscription state machine: active/tripling → paid plan; canceled
  with `cancel_at_period_end` and future period → keeps paid plan; terminal
  statuses → Free.
* **No live-mode credentials** anywhere in the repo.

### Frontend

* `frontend/app.js` (~570 lines): dashboard, tender library, upload modal,
  tender detail, bid workspace (loaded via `workspace.js`), Ask AI, company
  profile, account page with plan catalog and billing UI (checkout, portal,
  cancel).  Uses `credentials: 'include'` for cookie auth.
* `frontend/workspace.js` (~720 lines): bid preparation workspace, blockers
  panel, evidence picker, package workspace.
* No build step — files are served by FastAPI `StaticFiles`.
* No CSP nonce needed (no inline scripts/styles).

### Dockerfile

* `python:3.12-slim`, installs `gcc` + `libpq-dev`, copies `backend/` and
  `frontend/`, exposes 8000, runs uvicorn.  Does **not** set
  `APP_ENV` defaults or a non-root user; required env vars are only
  documented in `ARCHITECTURE.md`, not in the Dockerfile itself.
* `.dockerignore` exists and is comprehensive.
* `requirements-ai.txt` (sentence-transformers, faiss-cpu) is **not**
  installed in the Docker image — AI analysis runs without them (deterministic
  fallback), but no hosted provider support is wired.
* No `gunicorn` multi-worker setup; single uvicorn process (single-worker,
  not multi-worker).

### Test structure

* `tests/conftest.py` — sets env vars for isolated temp workspace before
  import.  No test modifies `APP_ENVIRONMENT` to production globally.
* Phase 10–14 tests live in `tests/test_phase1{0,1,2,3,4}.py`.
* `test_phase14.py` tests: readiness probe, storage factory, S3 writable path,
  AI provider defaults, production config validation.

---

## 2. Findings categorisation

### A. MUST IMPLEMENT NOW

| # | Finding | File/Area | Action |
|---|---|---|---|
| A1 | **No versioned migrations** — `initialize_database()` uses ad-hoc `ALTER TABLE` checks | `database.py` | Add Alembic; scaffold config; generate baseline migration from current schema; keep `create_all()` only for tests. |
| A2 | **Background analysis runs synchronously** in process-local `BackgroundTasks` — blocks the uvicorn worker, survives only as long as the process | `api.py` (`_run_analysis`, `create_tender`, `retry_analysis`, `reanalyze_tender`) | Introduce RQ worker; move `analyze_tender` and `export_package` to a job queue; add `JobStatus` model or use `AnalysisStatus` for job tracking. |
| A3 | **No structured logging** — `print()`/uvicorn default logs only | `main.py` | Add `logging.config` with JSON formatter; instrument key events (upload, analysis start/complete/fail, export, auth failure, webhook). |
| A4 | **Temp files from `ObjectStorage.path()` are never cleaned up** | `storage.py` | Add cleanup for temp files; use `tempfile.TemporaryDirectory` or explicit `delete()` calls in callers. |
| A5 | **No max upload body size at ASGI level** — only per-endpoint checks | `main.py` / server config | Configure uvicorn `--limit-concurrency` and/or middleware to reject oversized request bodies early. |
| A6 | **Dockerfile missing production defaults and non-root user** | `Dockerfile` | Add non-root user; document required env vars in comment block. |

### B. SHOULD IMPLEMENT BEFORE BETA

| # | Finding | File/Area | Action |
|---|---|---|---|
| B1 | **Hosted AI provider not implemented** — only Ollama or stub | `ai_provider.py` | Add `OpenAIProvider` implementing `LLMProvider`; select via `AI_PROVIDER=openai` + `OPENAI_API_KEY`. |
| B2 | **Single-worker uvicorn** — limits throughput | `Dockerfile` / deployment | Use `gunicorn -k uvicorn.workers.UvicornWorker` with N workers. |
| B3 | **No request ID / correlation** — debugging is hard | `main.py` middleware | Add `X-Request-ID` header; include in logs. |
| B4 | **No rate limiting** on auth endpoints | `auth_router` | Add `slowapi` or `starlette-limiter` with Redis. |
| B5 | **Export temp files not cleaned** after `FileResponse` | `package_service.py` | Ensure `output` temp file is deleted after response streams. |
| B6 | **No Prometheus metrics endpoint** | `main.py` | Add `prometheus-fastapi-instrumentator` for request/latency/queue metrics. |

### C. EXTERNAL SERVICE SETUP (before production)

| # | Service | Action |
|---|---|---|
| C1 | **PostgreSQL** | Provision managed Postgres; set `DATABASE_URL=postgresql+psycopg://...` |
| C2 | **S3-compatible storage** | Create bucket; configure CORS; set `STORAGE_BACKEND=s3`, `S3_BUCKET`, credentials |
| C3 | **Stripe live keys and Price IDs** | Replace test keys/Price IDs with production values |
| C4 | **Stripe webhook endpoint** | Configure production webhook URL in Stripe dashboard |
| C5 | **Domain name and TLS** | Procure domain; Caddy auto-provisions TLS via Let's Encrypt |
| C6 | **Redis** | Provision for RQ queue and optional rate-limit cache |
| C7 | **OpenAI/Anthropic API key** (if hosted LLM chosen) | Set `AI_PROVIDER=openai`, `OPENAI_API_KEY=...` |
| C8 | **Email provider** (SMTP/SES) | Configure for Phase 13 product features (verification, reset) |
| C9 | **Error tracking** (Sentry) | Set up free Sentry project for exception capture |

### D. CAN WAIT UNTIL AFTER LAUNCH

| # | Feature | Notes |
|---|---|---|
| D1 | Email verification | Product feature, not infrastructure |
| D2 | Password reset | Product feature |
| D3 | Team invitations / multi-user per workspace | Product feature (currently 1 workspace per user) |
| D4 | Advanced analytics dashboard | Observability polish |
| D5 | Multi-region deployment | Premature at early SaaS stage |
| D6 | CI/CD pipeline (GitHub Actions / Fly.io deploy) | Can be set up after manual deploy |
| D7 | Custom domain email for transactional emails | Use provider defaults initially |
| D8 | Webhook reconciliation job | Daily sync of Stripe subs vs. local DB |
| D9 | A/B testing framework | Premature |
| D10 | User feedback collection | Post-launch

---

## 3. Detailed audit: Database migrations (A1)

### Current mechanism

`database.py` → `initialize_database()`:
1. `Base.metadata.create_all(bind=engine)` — creates all tables from ORM models.
2. `inspector.get_columns()` checks → `ALTER TABLE ADD COLUMN` for missing columns.
3. Bootstrap user and workspace creation.

### Problems

* **Not versioned** — no downgrade path; no way to know which migrations have
  been applied in a production database.
* **`create_all()` will silently skip tables that already exist** but won't
  add columns that models define but the inline migrations don't cover.
* **No migration history table** — production can't be audited.
* **SQLite-only SQL** — `ALTER TABLE ... ADD COLUMN` syntax varies; PostgreSQL
  needs different handling for some column types.

### Recommendation: Introduce Alembic

#### Status: COMPLETE (Phase 15A)

#### Implementation

1. **Add to `requirements.txt`:** `alembic>=1.14.0`
2. **Create `alembic.ini`** with `sqlalchemy.url = sqlite:///./data/tender_ai.db`
   (matches local dev default; overridden by `DATABASE_URL` in production).
3. **Create `alembic/env.py`** that reads `settings.database_url` from the
   application config (same `Settings` model), so migrations run against the
   same database the app uses.
4. **Generate baseline migration** from current `Base.metadata` — this captures
  the current schema as the "zero" migration.  This is safe because:
  - Development databases are ephemeral (tests use temp DBs).
  - No production database exists yet.
  - `create_all()` continues to run in tests for speed; Alembic is the
    production path.
5. **Document the workflow:**
   - New model changes → `alembic revision --autogenerate -m "description"`
   - Deploy: `alembic upgrade head` before starting the app
   - Keep `initialize_database()` for test/standalone mode (it's harmless with
    Alembic; `create_all` is idempotent and the `ALTER TABLE` checks are
    guarded).

#### What NOT to do

* Do not remove `initialize_database()` — it's used in tests and local dev
  where running `alembic upgrade` is unnecessary friction.
* Do not generate migrations that drop columns or tables — preserve all
    existing data.

---

## 4. Detailed audit: Background jobs (A2)

### Current approach

`api.py` uses FastAPI's `BackgroundTasks`:
```python
background_tasks.add_task(_run_analysis, tender_id)
```
`_run_analysis()` opens its own `SessionLocal()`, runs `analyze_tender()`,
and on failure rolls back usage events.

### Problems

* **Process-local** — if the uvicorn worker dies, queued analyses are lost.
* **Single-worker** — with 1 uvicorn process, long analyses block other
  requests on the same worker.
* **No retry** — a failed analysis stays `FAILED`; re-running requires a
  manual API call.
* **No visibility** — no admin UI or metrics for queue depth.

### Recommendation: RQ (Redis Queue)

#### Rationale

* Python-native, minimal dependencies (`rq`, `redis`).
* No separate config format (unlike Celery beat, etc.).
* Workers are separate processes — uvicorn never blocks on analysis.
* Redis is the only new infrastructure requirement.
* Simple retry semantics built in.

#### Implementation plan (MUST — A2)

1. **Add to `requirements.txt`:** `rq>=2.2`, `redis>=5.0`
2. **Create `backend/app/jobs.py`** — thin wrapper that calls existing
  `analyze_tender` and `export_package` functions.  No business-logic changes.
3. **Modify `api.py`** — replace `background_tasks.add_task(...)` with
  `rq.Queue("analysis").enqueue_call("backend.app.jobs.run_analysis", ...)`
4. **Add `backend/app/worker.py`** — CLI entry point: `rq worker tender-ai`
5. **Dockerfile** — add a second target or document running the worker:
  separate worker container in compose.
6. **Add `docker-compose.yml`** — for local production simulation:
  api, worker, postgres, redis, caddy (optional).
7. **Tests** — existing tests call `create_tender` and then poll for
  `COMPLETED` status; the worker runs synchronously in tests via a `--sync`
  mode or by keeping `BackgroundTasks` as a fallback for test environments.

#### Service boundary

The cleanest service boundary is already `analyze_tender(tender_id: int, db: Session)`
in `analysis_service.py`.  The RQ worker calls this function in a loop.
`_run_analysis` in `api.py` becomes a thin adapter that creates its own
`SessionLocal` (as it already does).

---

## 5. Detailed audit: AI provider (B1)

### Current implementation

`ai_provider.py`:
* `LLMProvider` Protocol with `generate_grounded_json(evidence_by_category)`.
* `OllamaProvider` — exact same prompt/HTTP as original code.
* `_HostedProviderStub` — returns `None` for unknown providers.

`llm_service.py` — re-exports for backward compat.

`analysis_service.py:169`:
```python
llm_result = generate_grounded_json(evidence)
if llm_result:
    candidate = TenderAnalysis.model_validate(llm_result)
    if _llm_result_is_grounded(candidate, chunks):
        analysis = candidate
```

### Recommendation: Add OpenAI provider

#### Implementation plan (SHOULD — B1)

1. **Add to `requirements.txt`:** `openai>=1.50.0`
2. **New config fields** in `config.py`:
   - `openai_api_key: str = ""`
   - `openai_model: str = "gpt-4o-mini"`
3. **New `OpenAIProvider` class** in `ai_provider.py` that implements
  `LLMProvider`:
   - Same prompt as `OllamaProvider`.
   - Calls `openai.OpenAI().chat.completions.create(...)` with
    `response_format={"type": "json_object"}`.
   - Returns `None` on auth/network/parse failure (degrades to local analysis).
4. **Update `get_llm_provider()`** — if `AI_PROVIDER=openai`, return
  `OpenAIProvider`; if `AI_PROVIDER=ollama`, return `OllamaProvider`.
5. **No API keys committed** — read from env only.
6. **Graceful degradation** — if `openai_api_key` is empty and
  `AI_PROVIDER=openai`, raise a clear startup error in production mode.

#### Why OpenAI first

* Well-documented Python SDK.
* `gpt-4o-mini` is cost-effective for structured JSON extraction.
* The prompt is already designed to return JSON with `format: "json"`,
  making the OpenAI `response_format` parameter a natural fit.
* Anthropic/Claude can be added later via the same protocol.

---

## 6. Detailed audit: Storage (A4)

### Current implementation

`storage.py`:
* `ObjectStorage.path()` downloads to a `NamedTemporaryFile`; the temp file
  path is appended to `self.temp_files` but **never deleted**.
* `LocalFileStorage.path()` just returns the filesystem path (no temp files).

### Fix (MUST — A4)

1. Add a `cleanup()` method to the `DocumentStorage` Protocol.
2. `ObjectStorage.cleanup()` iterates `self.temp_files` and calls
   `os.unlink` on each, then clears the list.
3. Call `cleanup()` in the `finally` block of any endpoint that uses `.path()`
   (currently: `analysis_service.analyze_tender`, `evidence_service.store_pdf`).
4. Better: replace `NamedTemporaryFile` tracking with a context manager
   (`tempfile.NamedTemporaryFile` already supports `with` blocks).

### Recommendation

The `LocalFileStorage` and `ObjectStorage` implementations are otherwise
production-sufficient.  The only fix needed is temp-file lifecycle.

---

## 7. Detailed audit: Security

### Authentication

| Item | Current | Risk | Recommendation |
|---|---|---|---|
| Password hashing | `hashlib.scrypt`, 16-byte salt, n=2^14 | ✅ Good | Monitor scrypt params against OWASP guidance |
| Session token | `secrets.token_urlsafe(32)`, stored as HMAC hash | ✅ Good | None |
| Session storage | Server-side `UserSession` table | ✅ Good | None |
| Cookie flags | HttpOnly=True, SameSite=Lax, Secure=prod only | ✅ Good | None |
| Dev fallback | `development_user()` when `auth_required=False` | Medium | Only when auth disabled; documented |
| Rate limiting | ❌ None | High | SHOULD (B4) — brute-force on login/register |

### Authorization

| Item | Current | Risk | Recommendation |
|---|---|---|---|
| Workspace ownership | `enforce_request_ownership` dependency | ✅ Good | None |
| Cross-user isolation | Tests cover with 404s | ✅ Good | None |
| Billing scoping | `user`-dependent in all billing endpoints | ✅ Good | None |

### CORS / Trusted Hosts

| Item | Current | Risk | Recommendation |
|---|---|---|---|
| CORS | `CORSMiddleware` with `ALLOWED_ORIGINS` | ✅ Good | None |
| TrustedHost | `TrustedHostMiddleware` in prod | ✅ Good | None |
| CSP | Restrictive `default-src 'self'` | ✅ Good | None — no inline scripts/styles |

### File access / path traversal

| Item | Current | Risk | Recommendation |
|---|---|---|---|
| Upload filenames | UUID-based `stored_file_name` | ✅ Good | None |
| Path traversal | `_safe_path()` rejects `..` | ✅ Good | None |
| PDF header check | `content.startswith(b"%PDF")` | ✅ Good | None |
| Max upload size | Per-endpoint check | Low | Add ASGI-level guard (A5) |

### High-confidence fixes (Phase 15 scope)

| Fix | Priority | Description |
|---|---|---|
| **A5** | MUST | Add ASGI-level max body size middleware to reject oversized uploads early |
| **A4** | MUST | Fix temp file leak in `ObjectStorage.path()` and `writable_path()` |
| **A6** | MUST | Add non-root user to Dockerfile; document required env vars |

---

## 8. Detailed audit: Stripe production readiness

### Current state

* Stripe SDK configured via `STRIPE_SECRET_KEY` env var.
* Checkout uses internal plan → `STRIPE_PRICE_*` env var → Stripe Checkout.
* Webhook signature verified on raw body.
* Idempotency via `BillingEvent` table.
* Subscription state machine handles active/trialing/canceled/past_due/incomplete.
* `customer_for_user()` reuses or creates a single Stripe Customer per user.
* Tests mock `stripe.Customer.create`, `stripe.checkout.Session.create`,
  `stripe.billing_portal.Session.create`, `stripe.Webhook.construct_event`.
* **No live-mode credentials** anywhere in the repo.

### What's needed for production (live mode)

| Item | Action |
|---|---|
| Live API key | Replace `STRIPE_SECRET_KEY` test key with live key |
| Live Price IDs | Replace `STRIPE_PRICE_*` test prices |
| Webhook secret | Set `STRIPE_WEBHOOK_SECRET` for production endpoint URL |
| Webhook endpoint URL | Configure in Stripe dashboard (e.g. `https://tenderai.example/api/billing/webhook`) |
| Webhook monitoring | Log all `billing_events`; alert on `invoice.payment_failed` |
| Customer portal | Configure in Stripe dashboard (branding, allowed update operations) |
| Cancellation behavior | Already handled: `cancel_at_period_end` flag; terminal status → Free |
| Failed payment | `invoice.payment_failed` → `past_due` status surfaced in UI |
| Environment separation | `.env.production` vs `.env` (never commit real keys) |
| Reconciliation | Monthly audit: Stripe dashboard vs. `subscriptions` table |

### Risks

* No webhook retry handling — if a webhook is missed, the subscription state
  will be incorrect. **Recommendation:** add a periodic reconciliation job
  (daily sync of Stripe subscriptions vs. local DB) — this is a post-launch
  enhancement (D8).
* Checkout success does not activate the plan — **by design** (webhook is
  authoritative).  The frontend redirects to Stripe; the webhook fires
  asynchronously and updates `User.plan`.  This is correct but means there's
  a brief window where a paid user sees "Free" — acceptable.

---

## 9. Detailed audit: Observability

### Current state

* No application logging beyond uvicorn defaults.
* No structured logs (no request IDs, no JSON).
* No metrics endpoint.
* No error tracking (no Sentry).
* No alerting.

### Recommended minimal approach for early SaaS

| Concern | Tool | Scope |
|---|---|---|
| Structured logs | Python `logging` with JSON formatter | Log: request IDs, user IDs, analysis start/complete/fail, export, auth failures, webhook events |
| Error tracking | Sentry (free tier) | Capture unhandled exceptions with request context |
| Metrics | `prometheus-fastapi-instrumentator` | `/metrics` endpoint: request count, latency, DB pool, queue depth |
| Health checks | `/health` (liveness) + `/ready` (readiness) | ✅ Already exist |
| Alerting | Health-check monitoring (UptimeRobot / platform health) | Ping `/ready`; alert on 503 |

### Implementation plan

1. **Add JSON logging config** in `main.py`:
   - JSON formatter
   - `X-Request-ID` middleware (or custom)
   - Log every API request at INFO with method, path, status, duration
2. **Add Sentry SDK** (optional, can wait):
   - `sentry-asgi-middleware`
   - Capture exceptions with user context
3. **Add Prometheus metrics** (SHOULD — B6):
   - `prometheus-fastapi-instrumentator`
   - Instrument request count, latency, analysis duration
4. **Log events to plan**:
   - `INFO`: "Tender queued for analysis", "Analysis complete", "Export generated"
   - `WARNING`: "Analysis failed", "Webhook signature invalid", "Auth failed"
      - `ERROR`: unhandled exceptions (also sent to Sentry)

---

## 10. Deployment architecture

### Recommended: Docker Compose (local prod simulation) + Single VM / Managed Platform

```
                    ┌──────────────────┐
                    │     Caddy        │
                    │  (HTTPS via LE)  │
                    │  (port 443)      │
                    └────┬─────────┬───┘
                         │         │
              /api/* ────┘         └──── /static/*
                         │
                   ┌─────┴─────┐
                   │  uvicorn  │  (Gunicorn, 3 workers)
                   │  backend   │  (port 8000, internal)
                   └─────┬─────┘
                         │
                    ┌────┴────┐
                    │   RQ    │  (analysis + export jobs)
                    │  worker  │
                    └─────────┘
                         │
                    ┌────┴────┐
                    │ Redis   │  (queue + rate limiting cache)
                    │ (managed)│
                    └─────────┘

                    ┌──────────┐
                    │ PostgreSQL │  (managed: Supabase/RDS/Neon)
                    │           │
                    └──────────┘

                    ┌──────────┐
                    │   S3/R2   │  (document + evidence + export storage)
                    │           │
                    └──────────┘
```

### Deployment options (simplest first)

| Option | Pros | Cons | Recommendation |
|---|---|---|---|
| **Fly.io** | Built-in Postgres, Redis add-ons; `fly.toml` is simple; free tier | Vendor lock-in | ✅ Best for MVP |
| **Render** | Simple Postgres/Redis; auto-deploy from git | Limited custom domains on free | ✅ Good alternative |
| **Railway** | Simple, good free tier | Less battle-tested | ✅ Alternative |
| **Self-hosted VM** | Full control; cheapest | Manual TLS, backups, updates | ✅ If you can manage a server |
| **AWS ECS/Fargate** | Scalable | Complex, expensive | ❌ Overkill |

### What the Dockerfile needs

1. Add a **non-root user**.
2. **Document required env vars** in a comment block.
3. Consider **multi-stage build** to reduce image size (optional).
4. The current Dockerfile does NOT install `requirements-ai.txt` — this is
   correct for production (deterministic fallback), but if the OpenAI
   provider is used, `openai` must be added to `requirements.txt`.

### docker-compose.yml for local prod simulation

```yaml
services:
  api:
    build: .
    command: gunicorn -k uvicorn.workers.UvicornWorker -w 3 -b 0.0.0.0:8000 backend.app.main:app
    env_file: .env
    ports: ["8000:8000"]
    depends_on: [db, redis]

  worker:
    build: .
    command: rq worker tender-ai
    env_file: .env
    depends_on: [redis]

  caddy:
    image: caddy:2-alpine
    ports: ["80:80", "443:443"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy_data:/data
      - caddy_config:/config
    depends_on: [api]

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: tender_ai
      POSTGRES_USER: tender_ai
      POSTGRES_PASSWORD: CHANGE_ME
    volumes:
      - pg_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data

volumes:
  pg_data:
  redis_data:
  caddy_data:
    caddy_config:
```

---

## 11. Data safety

### Current state

| Data type | Where stored | Backup risk |
|---|---|---|
| Database (tenders, analysis, users, usage, billing) | SQLite (`data/tender_ai.db`) or PostgreSQL | High (local dev); Managed PG has automated backups |
| Uploaded tender PDFs | `uploads/` or S3 | Local dev: no backup; S3: versioning available |
| Company evidence PDFs | `company_evidence/` or S3 | Same as above |
| Embedding indexes | `data/indexes/` (JSON + FAISS) | Local dev: ephemeral (rebuilt on analysis); S3: not stored (regenerated) |
| Export ZIPs | `exports/` or S3 | Same as uploaded files |

### Key observations

* **Embedding indexes are NOT in the database** — they are files on disk or
  in S3.  If the index directory is lost, indexes are regenerated on the next
  `build_tender_index()` call.  This is acceptable — indexes are a cache.
* **No deletion cascade** is tested — `Tender` has
  `cascade="all, delete-orphan"` on its relationships, but the
  `tenders.stored_file_name` reference to storage is not cleaned up on
  tender deletion.
* **No account deletion endpoint** exists.
* **No data retention policy** is defined.

### Recommendations

| Action | Priority | Description |
|---|---|---|
| Fix storage cleanup on delete | MUST | When a `Tender` or `CompanyEvidenceDocument` is deleted, remove the file from storage |
| S3 lifecycle rules | SHOULD | Configure in bucket settings: delete versions older than N days; transition to Glacier |
| PostgreSQL backups | MUST (external) | Use managed Postgres automated backups |
| Define retention | CAN WAIT | 30-day log retention, 90-day export retention, etc. |
| Account deletion | CAN WAIT | Product feature — GDPR compliance |

---

## 12. Product gaps (non-infrastructure)

| Feature | Status | Notes |
|---|---|---|
| Email verification | ❌ Not implemented | Required for SaaS — users can register with fake emails |
| Password reset | ❌ Not implemented | Users are locked out if they forget their password |
| Account recovery | ❌ Not implemented | Tied to password reset |
| Team invitations | ❌ Not implemented | Currently 1 user → 1 workspace |
| Roles/permissions | ❌ Not implemented | All users have equal access |
| Usage alerts | ❌ Not implemented | Users don't know they're approaching limits |
| Invoice history | ❌ Not implemented | Stripe handles this via Customer Portal |
| 2FA | ❌ Not implemented | Optional for early SaaS |

> Email verification and password reset are **product features**, not
> infrastructure.  They require an email provider and are outside Phase 15's
> scope, but they block a true public SaaS launch.

---

## 13. Implementation checklist for Phase 15

### MUST (implement in this phase)

- [x] **Alembic migrations** (A1) — COMPLETE
   - Added `alembic>=1.14.0,<2.0` to `requirements.txt`
   - Created `alembic.ini` with `sqlalchemy.url = sqlite:///./data/tender_ai.db`
   - Created `alembic/env.py` reading `settings.database_url` (app + Alembic share one URL)
   - Generated baseline migration `c4724ba63c75_initial_schema.py` covering all 30 tables
   - Autogenerate diff on a migrated database produces zero changes (verified)
   - Documented: `alembic upgrade head` before app start in production; local SQLite DBs preserved and stamped as current

- [ ] **RQ background jobs** (A2)
  - Add `rq`, `redis` to `requirements.txt`
  - Create `backend/app/jobs.py` with `run_analysis(tender_id)`
  - Create `backend/app/worker.py` CLI entry point
  - Modify `api.py` to enqueue jobs
  - Add `docker-compose.yml` for local prod simulation
  - Keep `BackgroundTasks` path for tests (or add `--sync` mode)

- [ ] **Structured logging** (A3)
  - Add JSON logging config in `main.py`
  - Add `X-Request-ID` middleware
  - Log key events: upload, analysis start/complete/fail, export, auth, webhook

- [ ] **Temp file cleanup** (A4)
  - Fix `ObjectStorage.path()` and `writable_path()` to clean up temp files
  - Ensure `package_service` deletes temp ZIP after `FileResponse`

- [ ] **ASGI body size limit** (A5)
  - Add middleware or server config to reject bodies early

- [ ] **Dockerfile hardening** (A6)
  - Add non-root user
  - Document required env vars in comment block

### SHOULD (before beta)

- [ ] **OpenAI provider** (B1) — `openai` dep, config fields, provider class
- [ ] **Request ID middleware** (B3) — inject `X-Request-ID`
- [ ] **Multi-worker uvicorn** (B2) — document Gunicorn deployment
- [ ] **Prometheus metrics** (B6) — `prometheus-fastapi-instrumentator`
- [ ] **Rate limiting** (B4) — `slowapi`/`starlette-limiter` on auth

### External service setup (before production)

- [ ] Provision PostgreSQL (managed)
- [ ] Configure S3-compatible bucket
- [ ] Configure Stripe live keys + Price IDs
- [ ] Configure Stripe webhook endpoint URL
- [ ] Procure domain name + TLS
- [ ] Provision Redis
- [ ] Provision OpenAI API key (if hosted LLM path)
- [ ] Provision email provider
- [ ] Set up error tracking (Sentry)

### CAN WAIT (after launch)

- Email verification
- Password reset
- Team invitations / roles
- Webhook reconciliation job
- Analytics dashboard
- Multi-region deployment
- CI/CD pipeline

---

## 14. Phase 15 execution order

1. **Alembic** — COMPLETE (Phase 15A)
2. ~~**RQ worker**~~ — deferred to Phase 15B
2. **RQ worker** — enables durable background processing
3. **Structured logging + request IDs** — enables observability
4. **Temp file cleanup** — quick, important fix
5. **Dockerfile hardening** — quick, important fix
6. **OpenAI provider** — enables hosted LLM in production
7. **Rate limiting** — security hardening
8. **Prometheus metrics** — observability polish

### Test strategy

* All existing 68 tests must continue to pass.
* New Phase 15 tests:
  - `test_phase15a.py` — verify Alembic baseline matches `Base.metadata`, migration creates all tables, local dev data preserved, app still starts (11 tests, all passing — total suite now **79 passed**)

### Verification gate

Before Phase 15 is complete:
- [x] All 68 existing tests pass
- [x] All new Phase 15 tests pass
- [x] `pytest -q` exit code = 0
- [x] `node --check frontend/app.js` passes
- [x] `node --check frontend/workspace.js` passes
- [x] `/health` returns 200
- [x] `/ready` returns 200
- [x] Security headers present
- [x] Alembic `upgrade head` succeeds on a fresh SQLite database
- [x] Autogenerate diff on migrated database produces zero changes (migration matches models)
- [x] Existing local SQLite database is preserved (stamped, not rebuilt)
- [ ] RQ worker starts and can process a job (Phase 15B)

---

## 15. Phase 15A Completion Summary

Phase 15A (Database Migration Foundation) is COMPLETE. All MUST items for
this phase have been implemented and verified.

### What was done

1. **Alembic installed** — `alembic>=1.14.0,<2.0` added to `requirements.txt`.
2. **`alembic.ini`** created at project root with a non-sensitive default
   `sqlalchemy.url = sqlite:///./data/tender_ai.db`.
3. **`alembic/env.py`** reads `settings.database_url` (from `.env` / env vars)
   at runtime so Alembic always migrates the same database the app uses. A
   caller can still override the URL on the Config object (used by tests with
   temp databases).
4. **`alembic/script.py.mako`** — custom template using
   `%(rev)s_%(slug)s` naming.
5. **Initial migration** `c4724ba63c75_initial_schema.py` generated from
   `Base.metadata`. Covers all 30 model tables (users, workspaces,
   user_sessions, usage_events, subscriptions, billing_events, tenders,
   tender_requirements, tender_sources, tender_documents, tender_dates,
   tender_risks, tender_questions, tender_checklist_items, company_profiles,
   tender_assessments, tender_requirement_matches, tender_bid_actions,
   company_evidence_documents, company_evidence_chunks,
   tender_requirement_evidence, bid_workspaces, bid_requirements,
   bid_documents, bid_document_links, bid_clarifications, bid_notes,
   bid_packages, bid_package_items, bid_package_exports) with all primary
   keys, foreign keys, unique constraints, indexes, nullable/non-nullable
   behavior, and defaults preserved.
6. **`database.py`** — `run_migrations()` replaces the old ad-hoc
   `ALTER TABLE ... ADD COLUMN` logic with three cases:
   - **Fresh database:** `alembic upgrade head` creates the full schema.
   - **Legacy database** (tables exist, no `alembic_version`):
     `Base.metadata.create_all()` as an idempotent safety net, then
     `stamp head` to establish Alembic tracking — **no data lost**.
   - **Migrated database:** `alembic upgrade head` applies pending revisions.
7. **`main.py`** — `initialize_database()` calls `run_migrations()` then
   `_seed_development_data()`. No changes to main.py were required.
8. **Dockerfile** — copies `alembic/` and `alembic.ini` into the image.
9. **Tests** — `tests/test_phase15a.py` (11 tests) verifies migration
   creation, schema match, stamping, app startup, and local-dev seeding.
10. **Documentation** — README.md "Phase 15A" section, ARCHITECTURE.md,
    and this document updated.

### Migration workflow reference

| Action | Command |
|---|---|
| Create a new migration | `alembic revision --autogenerate -m "describe the change"` |
| Apply migrations | `alembic upgrade head` |
| Check current revision | `alembic current` |
| List revision history | `alembic history` |
| Roll back one revision | `alembic downgrade -1` |

In production, run `alembic upgrade head` before (or on) container start.
The app also runs migrations on startup via `initialize_database()`,
suitable for single-worker deployments.

### What was NOT changed

- `SCA_tender.pdf` — untouched.
- Phase 12 pricing/quota rules — untouched.
- Stripe billing rules — untouched.
- Authentication behavior — untouched.
- All existing application code outside `database.py` — untouched.

### Verification results

| Check | Result |
|---|---|
| `pytest -q` | **79 passed** (68 original + 11 Phase 15A) |
| SCA regression tests | **3 passed** |
| `node --check frontend/app.js` | PASS |
| `node --check frontend/workspace.js` | PASS |
| `uvicorn backend.app.main:app --reload` | Starts successfully |
| `GET /health` | HTTP 200 |
| `GET /ready` | HTTP 200 |
| Fresh SQLite DB via Alembic | All 30 tables + alembic_version created |
| Autogenerate on migrated DB | Zero diff (migration matches models) |
| Local SQLite DB preserved | `data/tender_ai.db` stamped at `c4724ba63c75` |

### PostgreSQL

PostgreSQL was **NOT** runtime-tested in this environment (no PostgreSQL
instance available). The Alembic migration uses standard SQLAlchemy types
that work across both SQLite and PostgreSQL. The `psycopg[binary]` driver is
already a dependency and is used when `DATABASE_URL` is set to
`postgresql+psycopg://...`. The `alembic/env.py` reads the database URL
from application settings, so production PostgreSQL deployments use the same
migration path. PostgreSQL should be tested in a staging environment before
real production deployment.

### What remains for production deployment (Phase 15B+)

- Durable background workers (RQ) — Phase 15B.
- Structured JSON logging + request IDs — Phase 15B.
- Temp file cleanup in storage/service layer — Phase 15B.
- ASGI body size limit middleware — Phase 15B.
- Dockerfile hardening (non-root user) — Phase 15B.
- OpenAI hosted provider — Phase 15B.
- Rate limiting on auth endpoints — Phase 15B.
- Prometheus metrics — Phase 15B.
- PostgreSQL runtime testing (staging).
- S3 lifecycle policies.
- Stripe live mode.
- CI/CD pipeline, error tracking (Sentry), webhook reconciliation.