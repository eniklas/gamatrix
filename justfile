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

# Bring up the local dev stack (app, DynamoDB, minio, mailhog)
up:
  docker compose up --build

# Tear down the local dev stack
down:
  docker compose down

# Create DynamoDB tables and S3 bucket locally, then seed test users
init-local:
  docker compose run --rm app python scripts/init_local.py

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

# Deploy infrastructure with CDK
deploy:
  cd infrastructure/cdk && CDK_DEFAULT_REGION=ca-central-1 npx cdk deploy --region ca-central-1

# Store IGDB API credentials in Secrets Manager
set-igdb-secret client_id client_secret:
  aws secretsmanager put-secret-value \
    --region ca-central-1 \
    --secret-id gamatrix/igdb \
    --secret-string "{\"client_id\":\"{{client_id}}\",\"client_secret\":\"{{client_secret}}\"}"
