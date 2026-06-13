# Agent Instructions for Gamatrix

This file is the canonical repository guidance for AI coding agents in this repo. Keep shared guidance here, and keep `CLAUDE.md` and `.github/copilot-instructions.md` as thin wrappers that point back to this file.

## Project Overview

Gamatrix is a Python web application that compares game libraries across multiple users. Users upload their GOG Galaxy SQLite database through the browser; the app parses it, stores ownership data in DynamoDB, and enriches each game with multiplayer metadata from the IGDB API. The web layer runs as a FastAPI app on AWS Lambda (via Mangum); background work (parsing uploads, enriching games) runs as separate Lambda functions triggered by S3 events and SQS messages.

## Build, test, and lint commands

Prefer the root `just` recipes when they match the task:

```bash
uv sync --extra dev          # install dev dependencies into the local uv-managed env
just dev                     # run the FastAPI app locally with reload
just env                     # create .env from .env-sample (then fill IGDB creds)
just up                      # start the full local stack with Docker Compose
just gen-fixtures db=<path>  # generate sample fixtures from YOUR GOG Galaxy DB (not committed)
just init-local              # create local tables/bucket and seed config/users
just seed-local              # seed the generated test users + sample game libraries
just bootstrap db=<path>     # one-shot: gen-fixtures + init-local + seed-local
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

## Local development with sample data

Get a working app — test users with overlapping, IGDB-enriched game libraries —
with no production resources and nothing on the host but Docker. Sample data is
generated from the developer's **own** GOG Galaxy DB; nothing binary is committed,
so a local GOG Galaxy DB is a hard prerequisite (it's the product's whole input).

```bash
just env                 # create .env, then set IGDB_CLIENT_ID / IGDB_CLIENT_SECRET in it
just up                  # start the stack (app, DynamoDB, minio, mailhog, worker)
just bootstrap db="C:/path/to/galaxy-2.0.db"   # gen fixtures + create tables/bucket + seed
# the worker (started by `just up`) enriches the seeded games via IGDB automatically;
# run `just worker` standalone if you brought the stack up without it.
# open http://localhost:8088 and log in as user1@example.com / changeme (first user = admin)
```

`gen-fixtures` (run by `bootstrap`, or standalone) derives slim SQLite fixtures from
the source DB via `scripts/sample_data/generate_fixtures.py`, writing them plus a
`seed_manifest.json` under `scripts/sample_data/` — all git-ignored. `seed-local` then
reuses the normal upload/ingest path (`ingest_db_file`) per fixture, so it exercises the
same code as a browser upload. Re-running `seed-local` is idempotent.

### Sample-data shape (configurable)

The default is 3 users with 20 games each. The generator parameters control the
ownership matrix: with **N** users, **G** games each, **C** common to all, and **P**
shared by every unique pair,

    uniques_per_user = G - C - P*(N-1)            # must be >= 0
    total_distinct   = C + P*comb(N, 2) + N*uniques_per_user

so the defaults (N=3, G=20, C=5, P=5) give 5 uniques each and 35 distinct games, with a
5-game overlap on *every* pair — enough for the compare view to show common and uncommon
games. Override per run:

```bash
just gen-fixtures db="C:/path/to/galaxy-2.0.db" users="4" games="25" common="6" pair="4" \
  usernames="ann@x.com,bob@x.com,cat@x.com,dan@x.com"
```

`usernames` is optional (auto `user1@example.com`…); the first user is always the admin.

Notes:
- `.env` is git-ignored and holds your IGDB credentials and JWT secret. `LOCAL_DEV=true`
  relaxes the production JWT check, so the sample secret is fine locally.
- Without a GOG Galaxy DB, `init-local` still creates the accounts but their libraries are
  empty, and `seed-local` tells you to run `gen-fixtures` first.
- Pass the DB path with **forward slashes** (e.g. `C:/Users/...`); `gen-fixtures` mounts
  that single file into the container and sets `MSYS_NO_PATHCONV=1` so Git Bash doesn't
  rewrite the container-side path.
- `docker-compose.yml` mounts `../gamatrix-configs` (the private deploy config) for
  maintainers; it is optional — the stack and sample seeding work without it.
- The `dynamodb-local` service persists its DB inside the image's WORKDIR
  (`/home/dynamodblocal`) rather than `/data`, so the named volume inherits that dir's
  unprivileged `dynamodblocal` (uid 1000) ownership and the service runs as a non-root
  user. If you still see "unable to open database file" (e.g. you have an older root-owned
  `dynamodb-data` volume from the previous `/data` layout), run `docker compose down -v`
  once to recreate the volume.
- Host-side tooling (tests/linters) uses `uv sync --extra dev`; the README has light
  [uv](README.md#uv) and [just](README.md#just) quickstarts.

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

**Routers:**
- `auth/routes.py` — login, logout, forgot/reset password (SES email in AWS, SMTP/MailHog locally)
- `games/routes.py` — `/games` page, `/games/table` HTMX fragment, `/api/jobs/{job_id}` polling endpoint, admin refresh routes
- `preferences.py` — ([src/gamatrix/preferences.py](src/gamatrix/preferences.py)) user preference saves (selected users, view mode, filters)
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

## Key conventions

- Route handlers should depend on the storage/auth abstractions instead of constructing clients inline. Use `Repository` via `get_repo()` / `get_repository()`, and reuse `get_s3()` / `get_queue()` for AWS-facing operations.
- Persisted game data is keyed by `release_key`, but the compare view deliberately merges duplicate titles by `(slug, owners)` so the same game across platforms collapses into one row only when the owner set is identical.
- Saved user preferences live on the user record in DynamoDB. Always merge stored preferences with `DEFAULT_PREFERENCES`, and keep the request overlay behavior consistent with `_parse_options()` in `games/routes.py` and `merge_preferences()` in [`games/preferences.py`](/src/gamatrix/games/preferences.py).
- Page routes and HTMX/API routes use different auth dependencies on purpose: `current_user()` redirects unauthenticated browsers to login, while `current_user_api()` returns `401` for fragment/API calls.
- Local development does not mirror AWS exactly. `/upload/complete` ingests inline only when `LOCAL_DEV=true`, and the local worker polls the jobs table because there is no local SQS event source.
- The repository layer normalizes DynamoDB types and identifiers for the rest of the app: emails are lowercased on write, `user_id` values are treated as strings, and floats are converted to `Decimal` before writes.
- Tests are built around the real `Repository` API with moto-backed AWS services (`tests/conftest.py`). Prefer seeding test data through repository methods rather than mocking internal DynamoDB calls.
- Versioning is automatic through `setuptools_scm` and release tags. Do not hand-edit version constants for normal changes.

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
