# Tender AI implementation plan

## 1. Architecture

Start with one FastAPI application serving a lightweight responsive frontend. Keep document ingestion, analysis, and tender APIs separate so background workers and a richer frontend can be introduced without changing the data contract.

## 2. Database

SQLite with SQLAlchemy models for `tenders`, `tender_requirements`, and `tender_sources` is the Phase 1 core. The model already carries analysis status and evidence fields. Phase 3 adds dates, documents, risks, tasks, questions, and AI analysis records; PostgreSQL migration can follow the same relational shape.

## 3. Backend

FastAPI owns validation, safe storage, JSON responses, and user-facing error messages. Uploaded files are accepted only as PDFs, checked for size and header, and renamed with an internal UUID. Detailed extraction and analysis services will be added under `backend/app/services/` in Phase 2.

## 4. Frontend

The first vertical slice uses server-served HTML, CSS, and JavaScript: sidebar navigation, dashboard metrics, tender library, upload modal, responsive tables, empty states, and settings placeholders. It avoids a chat-first interaction and keeps the primary workflow oriented around decisions and next actions.

## 5. AI/RAG pipeline

Phase 2 will extract text page by page with `pypdf`, preserve page metadata in chunks, generate embeddings, store a vector index, retrieve evidence for each analysis question, and validate structured JSON before persistence. Every claim will carry `evidence_type`, page, and snippet; absent information remains `UNKNOWN` rather than being guessed. Ollama/Qwen and sentence-transformers are integration points, not required for Phase 1 startup.

## 6. Security

Never trust original filenames or paths. Enforce PDF-only intake and a configurable size limit, use generated storage names, reject traversal in storage adapters, avoid returning filesystem paths, and return friendly API errors. Phase 10 adds user/workspace ownership checks, salted password hashes, bearer sessions, and account-scoped usage events. Malware scanning, cookie/session UX, and cloud hardening remain later work.

## 7. Testing

Phase 1 includes API tests for health, frontend availability, invalid uploads, and tender creation. Phase 10 adds authenticated cross-workspace isolation, storage traversal, usage, and migration compatibility tests. Phase 11 adds browser authentication/session tests. Phase 12 adds centralized plans, account-scoped monthly usage, and server-side quota tests. The suite runs against a temporary database and filesystem.

## 10. Phase 13 billing and subscriptions

Stripe is the external billing authority and is used through the official Python SDK in TEST MODE. The application flow is:

```text
Frontend
	-> authenticated backend billing endpoint
	-> Stripe-hosted Checkout or Customer Portal
	-> Stripe subscription
	-> signed webhook
	-> verified Subscription/BillingEvent state
	-> User.plan projection
	-> Phase 12 entitlements and limits
```

`User.stripe_customer_id` identifies the user's single Stripe Customer. `subscriptions` stores provider IDs and subscription lifecycle state. `billing_events` makes webhook delivery idempotent. The browser can request checkout for an internal plan key only; the backend resolves the configured Price ID. Checkout success does not activate a plan. Subscription lifecycle webhooks are authoritative, and invoice events update payment status without granting entitlements.

Active and trialing subscriptions map their verified Stripe price to the internal plan. Past-due and incomplete states preserve existing paid access temporarily and are surfaced to the account UI. Cancellation at period end preserves the current plan until `current_period_end`; a terminal canceled, unpaid, or incomplete-expired subscription resolves to Free. No tender, evidence, package, export, or other user data is deleted by billing changes.

Phase 12 usage remains calendar-month based and is intentionally independent of Stripe billing periods. This phase accepts Stripe's hosted subscription management behavior and does not implement custom proration calculations. Hosted Customer Portal handles payment methods, invoices, billing history, and plan changes. Stripe secrets are environment-only, webhook signatures are verified against the raw body, and no card, CVV, bank, or payout data is stored. Production schema changes are applied through versioned Alembic migrations (Phase 15A).

## 8. Phase 10 ownership and migration

The ownership chain is `User -> Workspace -> Tender` and `User -> Workspace -> CompanyEvidenceDocument/CompanyProfile`. Bid workspaces, assessments, requirements, clarifications, packages, exports, and generated artifacts derive ownership through their tender/workspace parents. A router-wide authorization dependency checks tender IDs and child-resource IDs server-side; collection endpoints also filter by the current user workspace.

The application previously used an idempotent startup compatibility migration because it had no Alembic dependency. This has been replaced by versioned Alembic migrations (Phase 15A). The initial migration represents the current model schema; existing local SQLite databases are detected and stamped as at the current revision without data loss, while fresh databases (including production PostgreSQL) are built by `alembic upgrade head`.

## 9. Phase 11 authentication and production-readiness

Browser authentication uses a random server-side session token whose hash is stored in `user_sessions`. Registration and login set an HttpOnly, SameSite=Lax cookie; production mode marks it Secure. Logout deletes the database session and cookie. Bearer headers remain supported for non-browser API clients. The frontend waits for `/api/auth/me` before showing the app shell, and redirects to the login/register gate after a 401.

`AUTH_REQUIRED=false` is a deliberate development escape hatch. `APP_ENVIRONMENT=production` requires `AUTH_REQUIRED=true`, and required auth refuses the development placeholder secret. `ALLOWED_ORIGINS` configures an explicit credentialed CORS allowlist; same-origin local use needs no CORS middleware. Authentication rate limiting is not implemented in-process because an in-memory limiter would not be reliable across workers; production edge/shared infrastructure must provide it.

## 10. Phase 12 plans, entitlements, and usage

Plan definitions live in `backend/app/plans.py`; users store only the selected plan key. The resolver supplies centralized limits for tender creation, AI questions, saved tenders, and team members. `usage_events` remains the single event architecture. Tender uploads and AI questions are counted inside the current calendar-month period; document processing and exports remain informational events.

The authenticated `/api/plan` and `/api/usage` endpoints expose plan configuration and account-scoped usage. Tender creation and Ask AI check allowances on the backend and return structured `usage_limit_reached` responses. Tender usage is reserved after persistence and removed when background analysis fails. Payment providers, billing periods, invitations, and checkout are outside this phase.

PostgreSQL compatibility is structurally reasonable: SQLAlchemy models use portable types, database URLs are configurable, and the startup migration uses Alembic for versioned schema management. Alembic is now the versioned migration tool (tested against SQLite); PostgreSQL testing against a real instance remains a TODO before production deployment. Local SQLite remains supported.

## 11. Phase 14 production infrastructure / cloud readiness

Phase 14 prepares the application for production deployment without altering the
local development workflow.  It addresses database portability, storage abstraction,
AI-provider abstraction, security hardening, containerisation, and documentation.

### Database

The SQLAlchemy engine configuration already accepts a configurable
`DATABASE_URL`, making the application PostgreSQL-compatible.  Production
connection pooling (`pool_pre_ping`, `pool_recycle`, `pool_size`, `max_overflow`)
is enabled for non-SQLite databases.  The inline `initialize_database()`
compatibility migration remains for local development; **Alembic is the
production migration path** and should be introduced and tested against a real
PostgreSQL instance before production deployment.  SQLite local development is
unchanged.

### File storage

A storage abstraction (`backend/app/storage.py`) now provides a
`DocumentStorage` protocol with two implementations:

* **`LocalFileStorage`** — used for local development (filesystem-backed).
* **`ObjectStorage`** — S3-compatible object storage for production, using
  `boto3` (imported lazily so local development does not require it).

The `get_storage_backend()` factory selects the backend based on
`settings.storage_backend` (`"local"` or `"s3"`).  Path-traversal protection
and per-user workspace isolation are preserved in both backends.  Users cannot
access another user's files because filenames use UUID keys and ownership is
enforced server-side via workspace queries.

### AI provider abstraction

A new `backend/app/services/ai_provider.py` module defines an `LLMProvider`
Protocol and provides an `OllamaProvider` implementation that preserves the
exact prompting and HTTP contract of the original `generate_grounded_json`
function.  The `get_llm_provider()` factory selects the provider based on
`settings.ai_provider` (defaults to `ollama`).  A `_HostedProviderStub`
placeholder demonstrates the abstraction and degrades gracefully to the
deterministic analysis path when no hosted provider is configured.
`backend/app/services/llm_service.py` re-exports the factory function so
existing callers (e.g. `analysis_service.py`) are unchanged.

### Background processing

The current architecture runs PDF extraction, analysis, embedding/index creation,
and package/export generation in the application process. Tender creation uses
FastAPI `BackgroundTasks`, which defers work until after the response but is not a
durable queue and must not be treated as production job delivery. A clean service
boundary is preserved (`backend/app/services/analysis_service.py` with
`analyze_tender()`), making migration to a durable worker possible in a future
phase without changing the API contract.

### Authentication & sessions

Sessions use HttpOnly, SameSite=Lax cookies; production mode marks them Secure.
`AUTH_SECRET` is required when `AUTH_REQUIRED` is enabled.  `APP_ENVIRONMENT=production`
requires `AUTH_REQUIRED=true`, a non-placeholder `AUTH_SECRET`, and
`ALLOWED_ORIGINS`/`ALLOWED_HOSTS` to be configured.  Production fail-safe
validation is enforced at module-load time.

### Security hardening

Production security headers are now applied to all responses via HTTP middleware:
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: strict-origin-when-cross-origin`, `X-XSS-Protection: 0`, and
a restrictive `Content-Security-Policy`.  A `/ready` readiness probe checks
database connectivity without exposing sensitive information.  Debug mode is
disabled in production.

### Stripe

Stripe configuration remains TEST MODE only.  No live-mode credentials exist.
The webhook endpoint (`/api/billing/webhook`) uses Stripe signature verification
on the raw request body — this is correct and must not change in production.
A `.env` file must be created from `.env.example` with real test-mode values
before end-to-end Stripe validation can be performed.

### Phase 14 operational boundaries

The production image includes the PostgreSQL `psycopg` driver and `boto3`; local
development does not require either service to be running. Set
`DATABASE_URL=postgresql+psycopg://...` for PostgreSQL and
`STORAGE_BACKEND=s3` plus `S3_BUCKET` for object storage. S3 exports use a local
temporary write path and upload the completed archive through the storage
adapter, while local exports continue to use the configured filesystem directory.

`/health` is a process liveness endpoint and `/ready` checks database
connectivity. In production, `ALLOWED_HOSTS` is enforced with trusted-host
middleware in addition to the existing CORS allowlist. The AI provider interface
supports local Ollama now; hosted providers, durable background processing,
object-storage lifecycle policies, and deployment-specific
observability remain external follow-up work.

### Containerisation

A minimal `Dockerfile` is provided:
* `python:3.12-slim` base image
* Installs OS dependencies (`gcc`, `libpq-dev`) for PostgreSQL support
* Installs Python dependencies from `requirements.txt`
* Copies `backend/` and `frontend/` source
* Runs `uvicorn backend.app.main:app --host 0.0.0.0 --port 8000`
* All secrets provided via runtime environment variables

A `.dockerignore` excludes `.venv/`, local data directories, `.env`, `__pycache__`,
and temporary scripts from the build context.

### Environment configuration

`.env.example` documents all configuration variable names.  Production secrets
(`DATABASE_URL`, `AUTH_SECRET`, `STRIPE_*`, `S3_*`) are provided via environment
variables at runtime — never committed to source control.

### Production startup

```
docker run -p 8000:8000 \
  -e DATABASE_URL=postgresql://... \
  -e AUTH_SECRET=<random-secret> \
  -e AUTH_REQUIRED=true \
  -e APP_ENVIRONMENT=production \
  -e ALLOWED_ORIGINS=https://tenderai.example \
  -e ALLOWED_HOSTS=tenderai.example \
  -e S3_BUCKET=<bucket> \
  -e S3_ACCESS_KEY_ID=... \
  -e S3_SECRET_ACCESS_KEY=... \
  tender-ai:latest
```

Or with uvicorn directly:

```
APP_ENVIRONMENT=production AUTH_REQUIRED=true AUTH_SECRET=<random-secret> \
  ALLOWED_ORIGINS=https://tenderai.example ALLOWED_HOSTS=tenderai.example \
  DATABASE_URL=postgresql://... uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

## 8. Development phases

- **Phase 1:** product foundation, upload, dashboard, tender library (current)
- **Phase 2:** extraction, evidence, embeddings, RAG, structured tender intelligence
- **Phase 3:** bid assessment, requirements, documents, dates, risks, tasks, clarifications
- **Phase 4:** background jobs, retries, source inspection, responsive and performance polish
- **Phase 5:** security review, type checking, deployment and operational hardening (migrations were fulfilled as Phase 15A — Alembic)
- **Phase 15:** production readiness audit — versioned migrations (Alembic) [Phase 15A complete — migration foundation in place], durable background jobs (RQ) [Phase 15B], structured logging [Phase 15B], storage lifecycle fixes [Phase 15B], Dockerfile hardening [Phase 15B], OpenAI provider [Phase 15B], rate limiting [Phase 15B], observability [Phase 15B].  See `PHASE15_AUDIT.md` for the full audit and implementation plan.
