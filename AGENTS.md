# Agent Instructions for Gamatrix

This file is the canonical repository guidance for AI coding agents in this repo. Keep shared guidance here, and keep `CLAUDE.md` and `.github/copilot-instructions.md` as thin wrappers that point back to this file.

## Build, test, and lint commands

Prefer the root `just` recipes when they match the task:

```bash
uv sync --extra dev          # install dev dependencies into the local uv-managed env
just dev                     # run the FastAPI app locally with reload
just up                      # start the full local stack with Docker Compose
just init-local              # create local tables/bucket and seed users
just worker                  # run the local background enrichment worker

just check                   # CI-equivalent checks: lint + typecheck + test
just lint                    # black --check . && flake8 src tests
just typecheck               # mypy
just test                    # pytest
uv run pytest tests/test_games.py
uv run pytest tests/test_games.py -k merge_cross_platform_duplicates

just format                  # black .
just build                   # build Lambda container images
just deploy                  # deploy CDK infrastructure

just set-igdb-secret <client_id> <client_secret> # Store IGDB API credentials in Secrets Manager (post-deploy)
```

## Architecture

Gamatrix is a FastAPI web app that runs locally under Uvicorn and in AWS Lambda via Mangum. `src/gamatrix/app.py` builds the app, mounts static assets, and registers the auth, games, preferences, and upload routers. `src/gamatrix/lambda_handler.py` is the AWS entry point.


**Data flow:**
The main data flow spans several modules:

1. Users upload a local GOG Galaxy SQLite database through the browser.
2. `src/gamatrix/upload.py` generates a presigned S3 POST so the file goes straight to object storage.
3. The parser/ingest path reads that SQLite file, extracts ownership and install data, and writes normalized records into DynamoDB through `src/gamatrix/storage/dynamo.py`.
4. `src/gamatrix/games/service.py` builds the comparison view from DynamoDB data, merges cross-platform duplicates, applies metadata overrides, and filters/sorts for the current request.
5. If selected games are stale or not yet enriched, `src/gamatrix/games/routes.py` creates an enrichment job. In AWS that job is processed asynchronously via SQS/Lambda; locally `scripts/local_worker.py` polls the jobs table and runs the same enricher code.
6. `src/gamatrix/igdb/enricher.py` groups releases by `igdb_key`, fetches IGDB metadata once per group, writes the results back to the games table, and updates job progress. The UI polls `/api/jobs/{job_id}` and updates via HTMX fragments.

Shared infrastructure concerns are centralized:

- `src/gamatrix/config.py` owns environment-driven settings, table names, and secret resolution.
- `src/gamatrix/storage/` contains the DynamoDB repository, S3 wrapper, and queue wrapper.
- `src/gamatrix/templating.py` sets up the shared Jinja environment used by the routers.

## Key conventions

- Route handlers should depend on the storage/auth abstractions instead of constructing clients inline. Use `Repository` via `get_repo()` / `get_repository()`, and reuse `get_s3()` / `get_queue()` for AWS-facing operations.
- Persisted game data is keyed by `release_key`, but the compare view deliberately merges duplicate titles by `(slug, owners)` so the same game across platforms collapses into one row only when the owner set is identical.
- Saved user preferences live on the user record in DynamoDB. Always merge stored preferences with `DEFAULT_PREFERENCES`, and keep the request overlay behavior consistent with `_parse_options()` in `games/routes.py` and `merge_preferences()` in `games/preferences.py`.
- Page routes and HTMX/API routes use different auth dependencies on purpose: `current_user()` redirects unauthenticated browsers to login, while `current_user_api()` returns `401` for fragment/API calls.
- Local development does not mirror AWS exactly. `/upload/complete` ingests inline only when `LOCAL_DEV=true`, and the local worker polls the jobs table because there is no local SQS event source.
- The repository layer normalizes DynamoDB types and identifiers for the rest of the app: emails are lowercased on write, `user_id` values are treated as strings, and floats are converted to `Decimal` before writes.
- Tests are built around the real `Repository` API with moto-backed AWS services (`tests/conftest.py`). Prefer seeding test data through repository methods rather than mocking internal DynamoDB calls.
- Versioning is automatic through `setuptools_scm` and release tags. Do not hand-edit version constants for normal changes.


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
