"""DB upload routes.

The browser uploads the GOG Galaxy SQLite file straight to S3 via a presigned
POST (so it never passes through the web Lambda's request-size limit). In AWS an
S3 event then triggers the db_parser Lambda. Locally there is no S3 event, so
/upload/complete ingests the file inline.
"""

from __future__ import annotations

import logging
import tempfile

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from gamatrix.auth.dependencies import (
    current_user,
    current_user_upload,
    get_repo,
)
from gamatrix.config import get_settings
from gamatrix.constants import UPLOAD_MAX_SIZE
from gamatrix.gogdb.ingest import ingest_db_file
from gamatrix.storage.dynamo import Repository
from gamatrix.storage.queue import get_queue
from gamatrix.storage.s3 import get_s3
from gamatrix.templating import authenticated_template

log = logging.getLogger(__name__)

router = APIRouter(tags=["upload"])


def _upload_key(user: dict) -> str:
    return f"uploads/{user['email']}.db"


@router.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request, user: dict = Depends(current_user)):
    return authenticated_template(request, "upload.html.jinja", {"user": user})


@router.get("/upload/presign")
def presign(user: dict = Depends(current_user_upload)):
    key = _upload_key(user)
    post = get_s3().presigned_upload(key, UPLOAD_MAX_SIZE)
    return JSONResponse({"key": key, "url": post["url"], "fields": post["fields"]})


@router.post("/upload/complete")
def complete(
    user: dict = Depends(current_user_upload),
    repo: Repository = Depends(get_repo),
):
    settings = get_settings()
    key = _upload_key(user)
    if not settings.local_dev:
        # In AWS the S3 event triggers the parser Lambda; nothing to do here.
        return JSONResponse({"status": "queued"})

    # Local dev: download and parse inline.
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        get_s3().download(key, tmp.name)
        user_id, job_id = ingest_db_file(tmp.name, repo, get_queue())
    # Link this account to its GOG user id on first upload.
    if str(user.get("user_id") or "") != user_id:
        repo.update_user(user["email"], {"user_id": user_id})
    return JSONResponse({"status": "ingested", "user_id": user_id, "job_id": job_id})
