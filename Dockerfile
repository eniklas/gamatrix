# syntax=docker/dockerfile:1

# ---- base: shared layer with uv and the project source ----
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1
WORKDIR /app
# Install uv (https://docs.astral.sh/uv/)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml README.md ./
COPY src ./src

# ---- dev: includes dev dependencies, used by docker-compose ----
FROM base AS dev
RUN uv pip install --system -e ".[dev]"
EXPOSE 8080
CMD ["uvicorn", "gamatrix.app:app", "--host", "0.0.0.0", "--port", "8080", "--reload"]

# ---- lambda-web: image for the API Gateway-backed web Lambda ----
FROM public.ecr.aws/lambda/python:3.12 AS lambda-web
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system .
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
