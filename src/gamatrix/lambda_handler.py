"""AWS Lambda entry point for the web app (API Gateway HTTP API + Mangum)."""

from __future__ import annotations

from mangum import Mangum

from gamatrix.app import app

handler = Mangum(app, lifespan="off")
