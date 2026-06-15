set dotenv-load

# Latest release version, taken from the most recent semver git tag. Tags are
# created automatically on merge to master (see .github/workflows/version.yml).
version := `git describe --tags --abbrev=0 --match "[0-9]*.[0-9]*.[0-9]*" 2>/dev/null || echo 0.0.0`
container_name := "gamatrix"

# List recipes
default:
  just --list

# Install dependencies into a local uv-managed virtualenv
install:
  uv sync --extra dev

# Create a local .env from .env-sample (idempotent). Fill in IGDB creds after.
env:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ -f .env ]]; then
    echo ".env already exists; leaving it untouched."
    exit 0
  fi
  cp .env-sample .env
  if command -v openssl >/dev/null 2>&1; then
    secret="$(openssl rand -base64 48 | tr -d '\n/+=')"
    tmp="$(mktemp)"
    sed "s|^JWT_SECRET=.*|JWT_SECRET=${secret}|" .env > "$tmp" && mv "$tmp" .env
    echo "Generated a random JWT_SECRET."
  fi
  echo "Created .env. Now set IGDB_CLIENT_ID and IGDB_CLIENT_SECRET in it."

# Bring up the local dev stack (app, DynamoDB, minio, mailhog)
up:
  docker compose up --build

# Tear down the local dev stack
down:
  docker compose down

# Create DynamoDB tables and S3 bucket locally, then seed test users
init-local:
  docker compose run --rm app python scripts/init_local.py

# Create DynamoDB tables and S3 bucket locally without seeding default users
init-local-empty:
  docker compose run --rm app python scripts/init_local.py --skip-default-users

# Seed 3 test users with sample game libraries (generated fixtures)
seed-local:
  docker compose run --rm app python scripts/seed_sample_data.py --hard-reset-existing-users

# One-shot: generate fixtures from your GOG DB, create tables/bucket, then seed.
#   just bootstrap db="C:/path/to/galaxy-2.0.db"
bootstrap db: (gen-fixtures db) init-local-empty seed-local

# Generate the sample fixtures from YOUR local GOG Galaxy DB (not committed).
# `db` is the absolute path to your galaxy-2.0.db (use forward slashes on
# Windows). The single file is mounted into the container; MSYS_NO_PATHCONV
# stops Git Bash from rewriting the container-side path. Tune the data shape
# with the optional args, e.g.:
#   just gen-fixtures db="C:/path/to/galaxy-2.0.db" users="4" games="25"
gen-fixtures db users="" games="" common="" pair="" usernames="":
  #!/usr/bin/env bash
  set -euo pipefail
  args=(--source /data/source.db --output scripts/sample_data)
  [[ -n "{{users}}" ]] && args+=(--num-users {{users}})
  [[ -n "{{games}}" ]] && args+=(--games-per-user {{games}})
  [[ -n "{{common}}" ]] && args+=(--common {{common}})
  [[ -n "{{pair}}" ]] && args+=(--pair-overlap {{pair}})
  [[ -n "{{usernames}}" ]] && args+=(--usernames "{{usernames}}")
  MSYS_NO_PATHCONV=1 docker compose run --rm \
    -v "{{db}}:/data/source.db:ro" app \
    python scripts/sample_data/generate_fixtures.py "${args[@]}"

# Run the local background job worker (stands in for the enricher Lambda)
worker:
  docker compose run --rm worker

# Run the app locally without Docker (expects local services + .env)
dev:
  uv run uvicorn gamatrix.app:app --host 0.0.0.0 --port 8088 --reload

# Run all checks (what CI runs)
check: lint typecheck test

lint:
  uv run black --check .
  uv run flake8 src tests

typecheck:
  uv run mypy

test:
  uv run pytest

# Auto-format code
format:
  uv run black .

# Build the Lambda container images
build:
  docker build --build-arg SETUPTOOLS_SCM_PRETEND_VERSION={{version}} --target lambda-web -t {{container_name}}-web:{{version}} .
  docker build --build-arg SETUPTOOLS_SCM_PRETEND_VERSION={{version}} --target lambda-enricher -t {{container_name}}-enricher:{{version}} .
  docker build --build-arg SETUPTOOLS_SCM_PRETEND_VERSION={{version}} --target lambda-parser -t {{container_name}}-parser:{{version}} .

# Fail fast if the private deploy config is missing, warn if it's off master.
# A missing cdk-config.yaml makes the stack synth WITHOUT a custom domain, which
# silently tears down the Route53 records and ACM cert on deploy (see config.py).
_check-deploy-config:
  #!/usr/bin/env bash
  set -euo pipefail
  config_dir="${GAMATRIX_CONFIG_DIR:-{{justfile_directory()}}/../gamatrix-configs}"
  config_file="$config_dir/cdk-config.yaml"
  if [[ ! -f "$config_file" ]]; then
    echo "ERROR: deploy config not found at $config_file" >&2
    echo "       Deploying without it removes the custom domain, deleting the" >&2
    echo "       Route53 alias records and ACM cert. Restore the gamatrix-configs" >&2
    echo "       checkout (or set GAMATRIX_CONFIG_DIR) before deploying." >&2
    exit 1
  fi
  if git -C "$config_dir" rev-parse --git-dir >/dev/null 2>&1; then
    branch="$(git -C "$config_dir" rev-parse --abbrev-ref HEAD)"
    if [[ "$branch" != "master" ]]; then
      where="branch '$branch'"
      [[ "$branch" == "HEAD" ]] && where="a detached HEAD"
      echo "WARNING: gamatrix-configs is on $where, not master." >&2
      echo "         Confirm this is the config you mean to deploy." >&2
    fi
  fi

# Deploy infrastructure with CDK
deploy: _check-deploy-config
  # cdk.json runs the app via ../../.venv/bin/python, so the cdk extra must be
  # present in .venv. `uv sync` prunes anything outside the synced set, so sync
  # dev + cdk together to keep the dev tools too.
  uv sync --extra dev --extra cdk
  cd infrastructure/cdk && CDK_DEFAULT_REGION=ca-central-1 npx cdk deploy --region ca-central-1

# Store IGDB API credentials in Secrets Manager
set-igdb-secret client_id client_secret:
  aws secretsmanager put-secret-value \
    --region ca-central-1 \
    --secret-id gamatrix/igdb \
    --secret-string "{\"client_id\":\"{{client_id}}\",\"client_secret\":\"{{client_secret}}\"}"
