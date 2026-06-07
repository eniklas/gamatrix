# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Gamatrix is a Python web application that compares game libraries across multiple users. Users upload their GOG Galaxy SQLite database through the browser; the app parses it, stores ownership data in DynamoDB, and enriches each game with multiplayer metadata from the IGDB API. The web layer runs as a FastAPI app on AWS Lambda (via Mangum); background work (parsing uploads, enriching games) runs as separate Lambda functions triggered by S3 events and SQS messages.

## Development Setup

```bash
uv sync --extra dev    # or: just install
```

The full local stack (app + DynamoDB local + MinIO + MailHog) runs via Docker Compose:

```bash
cp .env-sample .env   # fill in any blanks
just up               # bring up all services
just init-local       # create tables/bucket and seed test users (first time)
just worker           # run the local background job worker (separate terminal)
```

Or run the app directly without Docker (requires local services already running):

```bash
just dev              # uvicorn with --reload
```

## Key Commands

```bash
# Run all checks (what CI runs)
just check            # lint + typecheck + test

# Individual checks
just lint             # black --check + flake8
just typecheck        # mypy
just test             # pytest

# Run a single test file
uv run pytest tests/test_gogdb.py

# Auto-format
just format           # black .

# Build Lambda container images
just build

# Deploy infrastructure with CDK
just deploy

# Store IGDB API credentials in Secrets Manager (post-deploy)
just set-igdb-secret <client_id> <client_secret>
```

## Architecture

**Entry point:** `src/gamatrix/app.py` — creates the FastAPI `app`, mounts static files, and registers four routers. In AWS, `src/gamatrix/lambda_handler.py` wraps this app with Mangum.

**Routers:**
- `auth/routes.py` — login, logout, forgot/reset password (SES email in AWS, SMTP/MailHog locally)
- `games/routes.py` — `/games` page, `/games/table` HTMX fragment, `/api/jobs/{job_id}` polling endpoint, admin refresh routes
- `preferences.py` — user preference saves (selected users, view mode, filters)
- `upload.py` — S3 presigned POST generation for browser uploads

**Background Lambdas** (also run as local workers via `scripts/local_worker.py`):
- **Parser** (`gogdb/`) — triggered by S3 `OBJECT_CREATED`; reads the uploaded GOG Galaxy SQLite DB, extracts ownership and install status, writes to DynamoDB `user_libraries` and `games` tables.
- **Enricher** (`igdb/enricher.py`) — triggered by SQS; calls IGDB API for multiplayer metadata, writes results back to `games` table, updates job status.

**Storage (`storage/`):**
- `dynamo.py` — `Repository` class: all DynamoDB reads/writes. Handles Decimal serialization, pagination (`_scan`, `_query_all`), and table-name prefixing.
- `s3.py` — presigned POST generation for browser uploads; file downloads.
- `queue.py` — SQS `send_message` wrapper.

**IGDB client (`igdb/client.py`):**
- `IGDBClient` authenticates with the Twitch/IGDB API, looks up multiplayer metadata by GOG release key or slug, and respects rate limits with exponential backoff (`_RateLimiter` + `push_out()`).

**Config (`config.py`):**
- `Settings` (pydantic-settings): sourced from environment variables / `.env`. In AWS, secrets come from Secrets Manager — `igdb_secret_name` and `jwt_secret_name` point at the relevant secrets; `resolve_igdb_credentials()` and `resolve_jwt_secret()` fetch and cache them.
- A `model_validator` rejects startup with the default JWT secret in production (unless `LOCAL_DEV=true`).

**Templates (`src/gamatrix/templates/`):** Jinja2 + HTMX. `games.html.jinja` renders the main game list; `job_status.html.jinja` is polled via HTMX to show enrichment progress.

**Data flow:**
1. User uploads GOG Galaxy DB via the browser → S3 presigned POST → S3 `OBJECT_CREATED` → Parser Lambda
2. Parser writes games/ownership to DynamoDB
3. `/games` page load → `_maybe_enrich()` checks for stale/unenriched games → enqueues SQS job (deduped: returns existing job if one is already active)
4. Enricher Lambda processes the job, calls IGDB, writes metadata back
5. HTMX polls `/api/jobs/{job_id}` until the job completes, then swaps in the updated game table

**Tests:** `tests/`, run with `pytest`. `tests/conftest.py` provides fixtures. Coverage configured in `pyproject.toml`.

## Infrastructure

CDK stack in `infrastructure/cdk/gamatrix_stack.py`. See `infrastructure/cdk/README.md` for deploy instructions and post-deploy steps (IGDB secret, SES sandbox, seeding users).

DynamoDB tables (all `PAY_PER_REQUEST`, PITR on, name-prefixed with `TABLE_PREFIX`):
- `games` (PK: `release_key`)
- `users` (PK: `email`)
- `user_libraries` (PK: `user_id`, SK: `release_key`) + GSI on `release_key`
- `enrichment_jobs` (PK: `job_id`)
- `metadata_overrides` (PK: `slug`)
- `config` (PK: `key`) — locally holds hidden/single-player lists; in AWS these come from SSM

## Version

Versioning is automatic — there's nothing to bump by hand. setuptools_scm derives the package version from the latest git tag (`[tool.setuptools_scm]` in `pyproject.toml`; `__version__` in `src/gamatrix/__init__.py` reads it from the installed metadata).

On every merge to `master`, `.github/workflows/version.yml` computes the next semver from the latest tag and tags the merge commit. The bump is **patch** by default; add a label to the PR to change it:
- `new minor version` → bumps the minor component
- `new major version` → bumps the major component

(These two labels must exist in the repo for the workflow to pick them up.)

Because the Docker/CDK build context excludes `.git`, image builds receive the version via the `SETUPTOOLS_SCM_PRETEND_VERSION` build arg — `just build` passes it from `git describe`, and the CDK stack (`_project_version()`) passes it at synth time.
