"""Preference routes: view and persist per-user default options."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

from gamatrix.auth.dependencies import current_user, current_user_api, get_repo
from gamatrix.constants import DISPLAY_NAME_MAX_LENGTH, PROFILE_PIC_MAX_UPLOAD_SIZE
from gamatrix.games.preferences import merge_preferences
from gamatrix.helpers import pic_url
from gamatrix.images import process_profile_pic
from gamatrix.storage.dynamo import Repository
from gamatrix.storage.s3 import get_s3
from gamatrix.templating import templates

router = APIRouter(tags=["preferences"])


def clean_display_name(raw: str) -> str:
    """Validate and normalize a user-supplied display name.

    Collapses surrounding whitespace and enforces non-empty / max-length.
    Raises ValueError with a user-facing message if it doesn't qualify.
    """
    name = " ".join((raw or "").split())
    if not name:
        raise ValueError("Display name cannot be empty.")
    if len(name) > DISPLAY_NAME_MAX_LENGTH:
        raise ValueError(
            f"Display name must be {DISPLAY_NAME_MAX_LENGTH} characters or fewer."
        )
    return name


@router.get("/preferences", response_class=HTMLResponse)
def preferences_form(
    request: Request,
    user: dict = Depends(current_user),
    repo: Repository = Depends(get_repo),
):
    users = {str(u["user_id"]): u for u in repo.scan_users() if u.get("user_id")}
    return templates.TemplateResponse(
        request,
        "preferences.html.jinja",
        {
            "user": user,
            "users": users,
            "prefs": merge_preferences(user.get("preferences", {})),
        },
    )


@router.post("/preferences")
async def save_preferences(
    request: Request,
    user: dict = Depends(current_user_api),
    repo: Repository = Depends(get_repo),
):
    """Persist preferences. Accepts form posts from the prefs page or the
    auto-save on the filter bar."""
    form = await request.form()
    qp = request.query_params

    def flag(name: str) -> bool:
        source = form if name in form else qp
        return source.get(name) in ("true", "on", "1")

    selected = form.getlist("user") or qp.getlist("user")
    prefs = {
        "include_single_player": flag("single_player"),
        "installed_only": flag("installed_only"),
        "exclusive": flag("exclusive"),
        "show_keys": flag("show_keys"),
        "exclude_platforms": form.getlist("exclude") or qp.getlist("exclude"),
        "default_view": form.get("view", qp.get("view", "list")),
        "selected_users": selected if selected else "all",
    }
    repo.update_user(user["email"], {"preferences": prefs})
    return Response(status_code=204)


@router.post("/profile")
async def save_profile(
    request: Request,
    user: dict = Depends(current_user_api),
    repo: Repository = Depends(get_repo),
):
    """Update the signed-in user's display name."""
    form = await request.form()
    try:
        name = clean_display_name(str(form.get("username", "")))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    repo.update_user(user["email"], {"username": name})
    return JSONResponse({"username": name})


@router.post("/profile/pic")
async def upload_profile_pic(
    file: UploadFile = File(...),
    user: dict = Depends(current_user_api),
    repo: Repository = Depends(get_repo),
):
    """Accept an image upload, resize it, and store it as the user's pic."""
    raw = await file.read()
    if len(raw) > PROFILE_PIC_MAX_UPLOAD_SIZE:
        mb = PROFILE_PIC_MAX_UPLOAD_SIZE // (1024 * 1024)
        return JSONResponse(
            {"error": f"Image must be {mb} MB or smaller."}, status_code=400
        )
    try:
        png = process_profile_pic(raw)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    key = f"profile_img/{user['email'].lower()}.png"
    get_s3().put_bytes(key, png, "image/png")
    updated = int(time.time())
    repo.update_user(user["email"], {"pic_key": key, "pic_updated": updated})
    url = pic_url({**user, "pic_key": key, "pic_updated": updated})
    return JSONResponse({"pic_url": url})


@router.get("/profile_img/{user_id}")
def serve_profile_pic(
    user_id: str,
    _: dict = Depends(current_user),
    repo: Repository = Depends(get_repo),
):
    """Stream a user-uploaded profile pic from S3 (gated to logged-in users)."""
    target = repo.get_user_by_user_id(user_id)
    key = target.get("pic_key") if target else None
    if not key:
        return Response(status_code=404)
    data = get_s3().get_bytes(key)
    if data is None:
        return Response(status_code=404)
    # Safe to cache hard: the URL carries a ?v= cache-buster on each update.
    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )
