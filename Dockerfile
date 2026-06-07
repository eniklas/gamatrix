# syntax=docker/dockerfile:1

# Version is normally derived from the git tag by setuptools_scm, but the build
# context here has no git history, so callers pass it in instead. Defaults to
# 0.0.0 so ad-hoc builds still succeed.
ARG SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0

# ---- base: shared layer with uv and the project source ----
FROM python:3.12-slim AS base
ARG SETUPTOOLS_SCM_PRETEND_VERSION
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1 \
    SETUPTOOLS_SCM_PRETEND_VERSION=${SETUPTOOLS_SCM_PRETEND_VERSION}
WORKDIR /app
# Install uv (https://docs.astral.sh/uv/)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml README.md uv.lock ./
COPY src ./src

# ---- dev: includes dev dependencies, used by docker-compose ----
FROM base AS dev
# Install the exact, hash-pinned versions from uv.lock (--frozen fails if the
# lock drifts from pyproject.toml), then the project itself without re-resolving.
RUN uv export --frozen --no-emit-project --extra dev -o /tmp/requirements.txt \
 && uv pip install --system -r /tmp/requirements.txt \
 && uv pip install --system --no-deps -e .
EXPOSE 8080
CMD ["uvicorn", "gamatrix.app:app", "--host", "0.0.0.0", "--port", "8080", "--reload"]

# ---- lambda-web: image for the API Gateway-backed web Lambda ----
FROM public.ecr.aws/lambda/python:3.12 AS lambda-web
ARG SETUPTOOLS_SCM_PRETEND_VERSION
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${SETUPTOOLS_SCM_PRETEND_VERSION}
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml README.md uv.lock ./
COPY src ./src
# Install hash-pinned runtime deps from uv.lock, then the project (no re-resolve).
RUN uv export --frozen --no-emit-project -o /tmp/requirements.txt \
 && uv pip install --system -r /tmp/requirements.txt \
 && uv pip install --system --no-deps .
COPY src/gamatrix ${LAMBDA_TASK_ROOT}/gamatrix
CMD ["gamatrix.lambda_handler.handler"]

# ---- lambda-enricher: SQS-triggered IGDB enrichment worker ----
FROM lambda-web AS lambda-enricher
COPY lambdas/igdb_enricher/handler.py ${LAMBDA_TASK_ROOT}/
CMD ["handler.handler"]

# ---- lambda-parser: S3-triggered GOG DB parser ----
FROM lambda-web AS lambda-parser
COPY lambdas/db_parser/handler.py ${LAMBDA_TASK_ROOT}/
CMD ["handler.handler"]
