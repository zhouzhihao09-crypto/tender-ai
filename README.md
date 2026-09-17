# Tender AI

Tender AI is a source-aware tender intelligence workspace for SMEs. Phase 7 added local submission-package assembly, document preview, export validation, ZIP export, and export history. Phase 8 turns the validated system into a coherent product: one navigation model, an action-oriented dashboard, a lifecycle tender library, a "What is blocking this bid?" panel, an explicit Evidence Picker, and clearer bid preparation and submission-package workspaces. Phase 9 adds a responsive mobile-first presentation layer. Phase 10 adds the multi-user, workspace-ownership, authentication, usage, migration, and storage boundaries needed for a future SaaS without adding billing or cloud infrastructure.

## Architecture

- **Backend:** Python, FastAPI, SQLAlchemy, SQLite locally; database URLs remain environment-driven and PostgreSQL uses the psycopg driver with production pooling
- **Frontend:** server-served HTML/CSS/JavaScript, kept deliberately light for the first vertical slice
- **Document boundary:** uploaded PDFs are stored with generated internal filenames, extracted page by page, chunked with page identity, and indexed per tender
- **AI boundary:** optional sentence-transformers/FAISS and Ollama/Qwen are used when available; the default fallback is deterministic and local so tests and development do not require a running model
- **Evidence model:** `tender_sources` preserves page/snippet records for traceable claims; `evidence_type` distinguishes explicit facts, inferences, and unknowns
- **Bid workflow:** dashboard aggregates, conservative assessment, evidence-linked checklist items, status updates, re-analysis, risks, dates, clarifications, and source excerpts
- **Company fit:** one versioned company profile is compared against persisted tender requirements; matches, partial matches, no matches, unknowns, readiness, and verification actions are stored as assessment snapshots
- **Evidence Vault:** company PDFs are stored under generated filenames, extracted page by page, searchable locally, and linked to tender requirements through document/page/snippet relationships
- **Bid preparation:** each tender has one workspace that tracks the business decision, lifecycle, submission requirement statuses, document package links, clarifications, notes, missing items, timeline, and transparent final readiness
- **Submission package:** each bid workspace has one package which references requested documents and Evidence Vault files, validates readiness, previews extracted local text, and produces a factual ZIP manifest at export time
- **SaaS foundation:** `User` -> `Workspace` owns tenders, evidence, company profiles, bid data, packages, and usage events; protected API routes enforce ownership server-side
- **Storage boundary:** document, evidence, and export files use generated names through `LocalFileStorage` locally or the S3-compatible `ObjectStorage` adapter in production
- **Authentication:** Phase 11 provides browser registration/login/logout with server-managed HttpOnly cookies, plus bearer compatibility for API clients. Local development keeps `AUTH_REQUIRED=false`; authenticated deployments must use `AUTH_REQUIRED=true` and a non-placeholder secret.

## Run locally

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn backend.app.main:app --reload
```

Open <http://127.0.0.1:8000>.

## Phase 2 checks

1. Confirm the dashboard loads and the tender count is zero.
2. Select **New tender**, upload a real text-based PDF under 25 MB, and open the tender detail view.
3. Watch the status move from queued/processing to completed, then inspect summary, requirements, documents, dates, risks, clarifications, and source pages.
4. Ask a question in **Ask AI** and confirm the answer includes source page chips. Unknown questions must say the information was not found.
5. Try a scanned or empty PDF and confirm the UI shows a useful analysis failure with retry.
6. For local Qwen generation, install Ollama, pull `qwen2.5:7b`, and start Ollama. For optional embeddings, run `pip install -r requirements-ai.txt`.
7. Open <http://127.0.0.1:8000/docs> to inspect the API.

## Phase 3 checks

1. Open **Dashboard** and filter tenders by closing soon, risk, review, or analysis status.
2. Open a completed tender and review the assessment, requirements, dates, risks, clarifications, checklist, and source excerpts.
3. Mark checklist items as `IN_PROGRESS`, `DONE`, or `BLOCKED`; refresh and confirm the status persists.
4. Open **Tasks** to see evidence-linked actions across tenders.
5. Use **Re-analyze tender** and confirm the same tender is updated without losing checklist progress.

## Phase 4 checks

1. Open **Settings** and complete only the company fields you know; missing fields remain unknown.
2. Open a completed tender and review **Company fit assessment** beside the tender assessment.
3. Confirm every match shows the company field and tender source page, and that missing information is `UNKNOWN` rather than `NO_MATCH`.
4. Verify that an explicit company blocker can produce `NO_BID`, while incomplete company information produces `INSUFFICIENT_INFORMATION` or `REVIEW_REQUIRED`.
5. Update a verification action status and confirm it persists.

## Phase 5 checks

1. Open **Documents** and upload a company PDF, selecting a category and optional description.
2. Search the vault using text from the document and confirm the result includes the document and page number.
3. Upload a document with an explicit expiry date and confirm it is shown; documents without one show `Expiry not provided`.
4. Open a completed tender and create the company assessment. Supporting document counts and document/page summaries appear beside requirement matches.
5. Delete a vault document and confirm it no longer appears in search or future matching.

## Phase 6 checks

1. Open a completed tender to view its **Bid Preparation** workspace.
2. Set the lifecycle to `PREPARING`, record a `BID`, `NO_BID`, or `PENDING` decision, and add an internal decision note.
3. Update requirement and requested-document statuses. Link company evidence through `POST /api/bid-documents/{id}/evidence` and use `FOUND` or `REVIEW_REQUIRED` until a person confirms readiness.
4. Review the deterministic readiness explanation, missing items, explicit bid timeline, and clarification statuses.
5. Verify the same Evidence Vault document can be linked to several bid packages without duplicating the physical file.

## Phase 7 data flow

```text
Tender requirements + requested documents
	+ Bid workspace statuses + Vault evidence links
	-> Bid package items -> deterministic package validation
	-> local PDF preview / ZIP export + manifest + export history
```

## Phase 7 checks

1. Open a completed tender and review **Submission Package** above the bid workspace.
2. Confirm package readiness explains specific blockers. `Export package` is blocked until they are resolved.
3. Mark inclusion and document statuses deliberately; a found Vault file is not automatically `READY`.
4. Use **Preview** for a linked company PDF and inspect its extracted page text locally.
5. Export after resolving blockers. The ZIP contains `submission_manifest.json`, `README.txt`, and included company PDF copies.
6. Use **Export incomplete package** only when intentionally exporting a clearly incomplete, non-submission-ready package.

## Phase 8 checks

1. Navigate with the single sidebar: **Dashboard**, **Tenders**, **Tasks**, **Evidence Vault**, **Company Profile**. Technical concepts (embeddings, chunks, vector index) are not part of the UI.
2. Dashboard: action cards ("N tenders need your attention", "N bids missing required documents", …), upcoming deadlines, and recently updated tenders. Every number is derived from stored statuses; nothing is invented.
3. Tenders: filter by lifecycle (New, Under review, Bid decision pending, Preparing, Ready to submit, Submitted, No bid) or search; each row shows reference, closing, your decision, the separate AI recommendation, readiness, and blocker count.
4. Tender detail: tender facts, AI analysis (labelled as decision support), key dates, risks, suggested clarification questions, Company fit (strong / partial / unknown / concerns), Bid preparation, Submission package, Ask AI, and stored source evidence — each clearly separated.
5. Open a tender being prepared and read **"What is blocking this bid?"**. Only deterministic readiness causes are listed as blockers; every entry links to the relevant section.
6. Bid Preparation: record your own BID / NO_BID decision (the AI recommendation stays separate), update requirement and document statuses, and use **Find evidence…** to open the Evidence Picker.
7. Evidence Picker: suggestions are "potentially relevant" only. A link is created solely by your explicit selection and can be unlinked at any time; the Evidence Vault shows where each document is used.
8. Clarifications: draft, mark ready to send, mark sent, record the buyer's answer, and close — nothing is sent externally.
9. Submission Package: include/exclude items, set statuses and notes, add evidence explicitly, complete the final review, and export. Export stays blocked while deterministic blockers remain; exporting an incomplete package requires explicit confirmation and is recorded in the export history.
10. Run `pytest`: the suite runs in an isolated temporary workspace (see `tests/conftest.py`) so testing never pollutes real data, and `tests/test_sca_regression.py` protects the validated SCA tender behaviour when `SCA_tender.pdf` is present.

## Phase 9 checks

1. Use the same application at desktop, tablet, and phone widths around 375px, 390px, and 430px. The sidebar becomes a compact drawer, filters remain horizontally usable, and detail, bid preparation, evidence, and package controls stack without document-level horizontal overflow.
2. Review the Dashboard, Tenders library, Tender detail, Ask AI, Evidence Vault, Company Profile, Bid Preparation, and Submission Package screens at phone width. Empty, loading, error, source, and long-text states remain readable and actionable.
3. Ask AI keeps its mobile input and send action usable, announces loading/results, and shows a clear retryable error state. Source chips wrap instead of widening the page.
4. Run `node --check frontend/app.js` and `node --check frontend/workspace.js`, then run `pytest`. Browser smoke checks cover desktop and 375px/390px/430px layouts; the real SCA regression remains unchanged.

## Phase 10 checks

1. Local development still uses SQLite and the filesystem: `DATABASE_URL`, `UPLOAD_DIR`, `COMPANY_EVIDENCE_DIR`, `EXPORT_DIR`, and `INDEX_DIR` are environment-driven. Copy `.env.example` to a local environment file and replace only development values as needed.
2. Existing local rows are preserved. Startup creates a `local-dev@example.invalid` development user/workspace when needed, then backfills previously unowned tenders, evidence documents, and company profiles into `local-development`. This is an idempotent compatibility migration; it does not delete PDFs or database rows.
3. Register and authenticate through `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me`, and `POST /api/auth/logout`. Passwords use salted `scrypt` hashes; hashes are never returned. Set `AUTH_REQUIRED=true` to require bearer sessions for API access.
4. All tender-specific APIs, child bid resources, evidence resources, packages, and exports enforce workspace ownership on the backend. User A cannot read User B's tender, evidence, package, or export by changing an ID.
5. `GET /api/usage` exposes account-scoped usage events. Uploads, evidence processing, Ask AI questions, and package exports are measured, but no plans, limits, or billing are enforced.
6. Run `pytest`, `pytest tests/test_phase10.py`, `pytest tests/test_sca_regression.py`, `pytest tests/test_phase8.py`, and the two frontend `node --check` commands. The Phase 10 isolation tests use the same temporary test workspace setup and do not modify `SCA_tender.pdf`.

## Phase 11 checks

1. Development mode remains simple: activate `.venv`, run `.\.venv\Scripts\uvicorn.exe backend.app.main:app --reload`, and leave `AUTH_REQUIRED=false` to use the local development workspace. To exercise the authenticated flow, set `AUTH_REQUIRED=true` and provide a non-placeholder `AUTH_SECRET`.
2. The frontend checks `/api/auth/me` before revealing the application shell. Required-auth visitors see the login/create-account gate instead of a private-data flash. Registration, login, logout, expired sessions, invalid sessions, and duplicate accounts are covered by `tests/test_phase11.py`.
3. Browser sessions use an HttpOnly, SameSite=Lax cookie; production mode additionally marks it Secure. JavaScript does not receive or store the session token. Existing bearer headers remain supported for API clients and tests.
4. `ALLOWED_ORIGINS` is a comma-separated explicit CORS allowlist. Same-origin local development needs no CORS entry. Do not use `*` with credentialed production browser sessions.
5. Passwords are salted `scrypt` hashes. Login failures use the same generic invalid-credentials response. Authentication rate limiting is intentionally not implemented in-process; production deployment should provide it at the edge or through a shared rate-limit service.

## Phase 12 checks

1. Plans are centralized in `backend/app/plans.py`: Free, Starter, Professional, and Business define tender, AI question, saved-tender, and team-member entitlements. New registered users default to Free; the local development escape-hatch account uses Business capacity so the unauthenticated regression workspace remains usable.
2. `GET /api/plan` returns the authenticated user's plan and entitlements. `GET /api/plans` returns product configuration with `payment_available: false`. No payment provider or checkout is implemented.
3. `GET /api/usage` returns current calendar-month usage, limits, remaining allowances, and the existing event totals/history. Only tender uploads and AI questions consume Phase 12 allowances; other Phase 10 event types remain informational.
4. Tender and Ask AI limits are enforced server-side using the authenticated session. Limit failures return structured `usage_limit_reached` responses. Failed tender analysis does not consume a tender allowance, and retrying a failed tender is quota-checked.
5. The Account & usage screen displays the current plan, usage metrics, plan comparison, and the explicit message that payment is not yet available. Existing data is retained when entitlements change; team-member limits are represented but invitations are future work.

## Phase 13 checks

1. Stripe Billing and Stripe-hosted Checkout are supported in TEST MODE only. Configure the `STRIPE_*` values from `.env.example`; Free accounts continue working when Stripe is not configured.
2. The flow is `Frontend -> our backend -> Stripe Checkout -> Stripe Subscription -> signed Stripe webhook -> verified billing state -> User.plan -> Phase 12 entitlements`. The browser never supplies a Stripe Price ID or payment result, and no card or bank details are stored.
3. `POST /api/billing/checkout` accepts only an internal paid plan key and resolves its Price ID from environment configuration. `POST /api/billing/portal` creates a hosted Customer Portal session. `POST /api/billing/cancel` requests cancellation at period end.
4. Subscription records store Stripe customer/subscription/price IDs, plan, status, billing period, and cancellation state. Billing event IDs are stored for idempotency. `customer.subscription.*` events synchronize plan state; checkout completion only records customer linkage. Invalid webhook signatures are rejected.
5. Active/trialing subscriptions grant the mapped paid plan. Past-due/incomplete status preserves an already active paid plan while surfacing the billing warning. Cancellation at period end preserves paid access until the recorded end date; terminal cancellation falls back to Free without deleting data.
6. Stripe billing periods and Phase 12 calendar-month usage periods remain separate. Proration and plan changes are handled through the hosted Stripe portal; custom tax, payout, invoice, PayNow recurring, and production deployment systems are not implemented.

## Phase 14 checks

1. Local development remains SQLite/filesystem/Ollama based. Use `STORAGE_BACKEND=local`, `DATABASE_URL=sqlite:///./data/tender_ai.db`, and `AI_PROVIDER=ollama` for the local workflow.
2. PostgreSQL deployments use `DATABASE_URL=postgresql+psycopg://...`; SQLAlchemy enables connection liveness checks and pooling for non-SQLite databases. Schema changes are applied through versioned Alembic migrations (`alembic upgrade head`); local development databases are auto-migrated or stamped on first start.
3. S3-compatible storage is selected with `STORAGE_BACKEND=s3` and `S3_BUCKET`. Uploads and evidence reads use the storage adapter, and submission ZIP exports are written to a temporary file then uploaded through the adapter before being returned.
4. `/health` is a liveness check and `/ready` verifies database connectivity. Production requires `APP_ENVIRONMENT=production`, `AUTH_REQUIRED=true`, a non-placeholder `AUTH_SECRET`, `ALLOWED_ORIGINS`, and `ALLOWED_HOSTS`; trusted-host middleware rejects other hosts.
5. `AI_PROVIDER=ollama` preserves the local provider. Other provider names currently use a safe no-op stub and deterministic analysis fallback; a hosted provider implementation is future work. Tender analysis still runs in process-local FastAPI background work and needs a durable worker before high-volume production use.

## Phase 15A — Database migration foundation

Versioned schema migrations are now managed by [Alembic](https://alembic.sqlalchemy.org/).

### Local development (SQLite)

On first start the application creates the schema automatically via Alembic's
initial migration.  Existing local databases (created by the pre-15A
`create_all()` path) are preserved and stamped as at the current migration
revision — no data is lost and tables are not recreated.

```powershell
# Run the app — migrations + data seeding happen automatically on startup
uvicorn backend.app.main:app --reload
```

### Production (PostgreSQL)

Before the first container start (or after pulling a new image with schema
changes), apply pending migrations explicitly:

```bash
# Option A: run inside the container before starting the app
alembic upgrade head

# Option B: the app also runs migrations on startup via initialize_database()
#           (suitable for single-worker deployments)
```

The `DATABASE_URL` environment variable is the single source of truth for
both the application and Alembic.  The `alembic.ini` file contains only a
non-sensitive default; the real URL is injected at runtime via `env.py`,
which imports `settings.database_url`.

### New migrations

When models change, generate a migration script:

```bash
alembic revision --autogenerate -m "describe the change"
alembic upgrade head           # apply locally to verify
```

Review the generated diff carefully before committing — the autogenerate
feature inspects the database and models, so it may include spurious
`ALTER TABLE` statements for SQLite type differences.

### Inspecting migration status

```bash
alembic current   # show the current revision of the database
alembic history   # list all revisions (most recent first)
alembic branches  # show any pending branch points
alembic downgrade -1   # roll back one revision (use with care)
```

## Delivery phases

1. Product foundation and tender intake
2. PDF extraction, evidence, RAG, and structured tender intelligence
3. Bid workspace: requirements, documents, dates, risks, tasks, clarifications
4. Company profile matching and conservative assessment snapshots
5. Company Evidence Vault with reusable, linkable evidence
6. Dedicated Bid Preparation workspace with lifecycle, decisions, and traceability
7. Submission package assembly, preview, export gating, and export history
8. Productization/UX: navigation, action dashboard, tender library, blockers panel, Company fit, Evidence Picker, package workspace, tests, and cleanup
9. Responsive mobile-first UX across dashboard, tender detail, Ask AI, evidence, bid preparation, and submission package
10. SaaS foundation: users, workspaces, ownership authorization, local storage adapters, environment configuration, usage events, and compatibility migration
11. Authenticated frontend, browser session management, protected app gate, configurable CORS, and production-auth configuration guard (current)
12. Plans, usage limits, credits, entitlements, and account usage display (current)
13. Stripe test-mode Checkout, subscriptions, signed webhooks, hosted billing portal, cancellation, and billing state (current)
14. Production/cloud readiness boundaries: PostgreSQL driver/pooling, S3-compatible storage adapter, AI provider interface, security/readiness probes, container configuration, and deployment documentation (current)

## V1 deployment

This section documents the configuration a V1 deployment actually needs.
All configuration is read from environment variables (or a local `.env`
file).  Nothing is baked into the image; secrets are never committed.

### 1. Install dependencies / build the image

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

For the Docker image:

```powershell
docker build -t tender-ai:v1 .
```

The image runs as the unprivileged `appuser` (UID 1000).  The writable
data directories (`/app/data`, `/app/uploads`, `/app/company_evidence`,
`/app/exports`) are created and owned by `appuser` at build time.

### 2. Configure environment variables

Copy `.env.example` to `.env` and fill in the values below.  The file
contains placeholders only — never paste a real secret into it.

### 3. Authentication secret

- `AUTH_REQUIRED=true` for any authenticated deployment.
- `AUTH_SECRET` must be a non-placeholder random value.  The
  development placeholder `development-only-change-me` is rejected at
  import time when `AUTH_REQUIRED` is enabled or `APP_ENVIRONMENT` is
  `production`.
- `ALLOWED_ORIGINS` and `ALLOWED_HOSTS` are required in production.

### 4. Database

- `DATABASE_URL` selects the database.  Local development defaults to
  `sqlite:///./data/tender_ai.db`.
- PostgreSQL is supported/configured via `psycopg` and the production
  SQLAlchemy pool settings (`pool_pre_ping`, `pool_recycle`, `pool_size`,
  `max_overflow`).  PostgreSQL runtime behaviour has **not** been tested
  in this environment — it requires an external PostgreSQL instance.
- Schema changes are managed by Alembic.  Apply migrations before
  starting the application:

  ```bash
  alembic upgrade head
  ```

  The application also runs migrations at startup via
  `initialize_database()`, which is suitable for single-worker
  deployments.  Existing local SQLite databases are preserved and
  stamped as current rather than rebuilt.

### 5. Storage

- `STORAGE_BACKEND=local` (default) uses the filesystem under
  `UPLOAD_DIR`, `COMPANY_EVIDENCE_DIR`, and `EXPORT_DIR`.
- `STORAGE_BACKEND=s3` uses the S3-compatible `ObjectStorage` adapter.
  Requires `S3_BUCKET`; `S3_ENDPOINT_URL`, `S3_REGION`,
  `S3_ACCESS_KEY_ID`, and `S3_SECRET_ACCESS_KEY` are optional.
  S3 runtime behaviour has **not** been tested in this environment — it
  requires an external S3-compatible service.

### 6. AI provider

Two options are supported.

**Option A — local Ollama (default):**

- `AI_PROVIDER=ollama`
- `OLLAMA_URL` (default `http://127.0.0.1:11434`)
- `OLLAMA_MODEL` (default `qwen2.5:7b`)
- `OLLAMA_TIMEOUT_SECONDS` (default `30`)

When Ollama is unreachable the provider returns `None` and the
application falls back to deterministic, local analysis.  This is the
intended behaviour and requires no configuration.

**Option B — hosted OpenAI:**

- `AI_PROVIDER=openai`
- `OPENAI_API_KEY` — **required**; the configuration guard rejects
  startup without it.
- `OPENAI_MODEL` (default `gpt-4o-mini`)

The API key must never be placed in source code, tests, `.env.example`,
or any committed file.  When OpenAI is unreachable or returns unusable
output the provider returns `None` and the deterministic analysis path
is used, exactly as with Ollama.

Real OpenAI API validation could **not** be performed in this
environment because no `OPENAI_API_KEY` was configured.

### 7. Background jobs / Redis

- Durable background jobs use RQ.  Set `REDIS_URL` (e.g.
  `redis://localhost:6379/0`) to enable them.
- When `REDIS_URL` is not set, analysis jobs run through the
  application's `BackgroundTasks` fallback.  This is safe and is the
  behaviour used by the test suite.
- Start the worker separately if RQ is enabled:

  ```bash
  python -m backend.app.worker
  ```

  The worker listens on the `tender-ai` queue and requires
  `REDIS_URL`.  Redis runtime behaviour has **not** been tested in this
  environment — it requires an external Redis instance.

### 8. Verify the deployment

- `GET /health` — liveness probe.  Returns `{"status": "ok"}`.  It is
  intentionally lightweight and does not check dependencies.
- `GET /ready` — readiness probe.  Returns `{"status": "ready"}` when
  the database connection is alive, otherwise `503
  {"status": "unavailable"}`.  Use this to gate traffic at the
  orchestrator or load balancer.

Every HTTP response carries an `X-Request-ID` header for log
correlation.

### 9. Stripe billing

Stripe is configured in test mode only.  Production billing requires
live keys, live Price IDs, a configured webhook endpoint, and a
configured Customer Portal — all external to this repository.  No
live-mode credentials are present anywhere in the project.

## Phase 11 limitations and future production work

Implemented now: browser registration/login/logout, HttpOnly session cookies, session expiry/revocation, protected frontend bootstrap, server-side ownership checks, configurable CORS, and local SQLite/filesystem compatibility.

Future work: PostgreSQL deployment testing, shared authentication rate limiting, password reset/email verification, cookie rotation/CSRF review for cross-origin deployments, durable background workers, hosted AI provider integration, production Stripe rollout and operational reconciliation, subscription billing periods, and team invitations. Versioned Alembic migrations are now in place (Phase 15A). No payout API, card storage, or custom payment processor is implemented.

