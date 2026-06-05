set dotenv-load

version := `grep "^version =" pyproject.toml |awk -F\" '{print $2}'`
container_name := "gamatrix"

# List recipes
default:
  just --list

# Increment version; pass in "major" or "minor" to bump those
bump-version type="patch":
  #!/usr/bin/env bash
  set -euo pipefail
  old_version={{version}}
  IFS=. components=(${old_version##*-})
  major=${components[0]}
  minor=${components[1]}
  patch=${components[2]}
  type={{type}}
  case $type in
    major|MAJOR)
      new_version="$((major+1)).0.0";;
    minor|MINOR)
      new_version="$major.$((minor+1)).0";;
    patch|PATCH)
      new_version="$major.$minor.$((patch+1))";;
    *)
      echo "Bad type: $type"
      echo "Valid types are major, minor, patch"
      exit 1;;
  esac
  echo "Bumping version from $old_version to $new_version"
  sed -i "s/^version =.*/version = \"$new_version\"/" pyproject.toml

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
  docker build --target lambda-web -t {{container_name}}-web:{{version}} .
  docker build --target lambda-enricher -t {{container_name}}-enricher:{{version}} .
  docker build --target lambda-parser -t {{container_name}}-parser:{{version}} .

# Deploy infrastructure with CDK
deploy:
  cd infrastructure/cdk && uv run cdk deploy

# Tag commit with current release version
git-tag:
  #!/usr/bin/env bash
  if [ ! "$(git diff --quiet --exit-code)" ]; then
    git commit -am "bump version"
    git tag --annotate --message="bump to version {{version}}" "{{version}}"
    git push
    git push --tags
  fi
