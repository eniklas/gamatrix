"""Preference routes: view and persist per-user default options."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from gamatrix.auth.dependencies import current_user, current_user_api, get_repo
from gamatrix.constants import (
    DISPLAY_NAME_MAX_LENGTH,
    PLATFORMS,
    PROFILE_PIC_MAX_UPLOAD_SIZE,
)
from gamatrix.games.preferences import DISPLAY_MODES, merge_preferences
from gamatrix.helpers import pic_url
from gamatrix.images import process_profile_pic
from gamatrix.storage.dynamo import Repository
from gamatrix.templating import authenticated_template

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
    return authenticated_template(
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
    # The platform control is an inclusive list ("Include platforms"); store the
    # complement so the saved pref stays in the service's exclude vocabulary.
    included = set(form.getlist("include") or qp.getlist("include"))
    exclude_platforms = [p for p in PLATFORMS if p not in included]
    current = merge_preferences(user.get("preferences", {}))
    requested_mode = form.get("display_mode")
    if requested_mode == "system":
        display_mode = None
    elif requested_mode in DISPLAY_MODES:
        display_mode = requested_mode
    else:
        display_mode = current["display_mode"]
    prefs = {
        "include_single_player": flag("single_player"),
        "installed_only": flag("installed_only"),
        "exclusive": flag("exclusive"),
        "show_keys": flag("show_keys"),
        "exclude_platforms": exclude_platforms,
        "default_view": form.get("view", qp.get("view", "list")),
        "selected_users": selected if selected else "all",
        "display_mode": display_mode,
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


async def _read_body_capped(request: Request, limit: int) -> bytes | None:
    """Read the raw request body in chunks, aborting as soon as it crosses
    `limit`. Returns None if oversized, so an attacker can't force the whole
    body to be buffered to memory/disk before the size is checked."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/profile/pic")
async def upload_profile_pic(
    request: Request,
    user: dict = Depends(current_user_api),
    repo: Repository = Depends(get_repo),
):
    """Accept a raw image body, resize it, and store it as the user's pic.

    The image is sent as the raw request body (not multipart) so it can be read
    with an early size cap instead of being buffered in full first.
    """
    raw = await _read_body_capped(request, PROFILE_PIC_MAX_UPLOAD_SIZE)
    if raw is None:
        mb = PROFILE_PIC_MAX_UPLOAD_SIZE // (1024 * 1024)
        return JSONResponse(
            {"error": f"Image must be {mb} MB or smaller."}, status_code=400
        )
    try:
        png = process_profile_pic(raw)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    user_id = str(user["user_id"])
    repo.put_profile_pic(user_id, png)
    updated = int(time.time())
    repo.update_user(user["email"], {"pic_updated": updated})
    url = pic_url({**user, "pic_updated": updated})
    return JSONResponse({"pic_url": url})


@router.get("/profile_img/{user_id}")
def serve_profile_pic(
    user_id: str,
    _: dict = Depends(current_user),
    repo: Repository = Depends(get_repo),
):
    """Serve a user-uploaded profile pic (gated to logged-in users).

    A single keyed read from the profile_pics table — no full-table scan.
    """
    data = repo.get_profile_pic(user_id)
    if data is None:
        return Response(status_code=404)
    # private (not public): the route is auth-gated, so shared proxy/CDN
    # caches must not hold it. The ?v= cache-buster keeps the browser cache
    # fresh across updates.
    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )
