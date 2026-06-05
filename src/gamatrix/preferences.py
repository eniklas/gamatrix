"""Preference routes: view and persist per-user default options."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from gamatrix.auth.dependencies import current_user, current_user_api, get_repo
from gamatrix.games.preferences import merge_preferences
from gamatrix.storage.dynamo import Repository
from gamatrix.templating import templates

router = APIRouter(tags=["preferences"])


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
        "exclude_platforms": form.getlist("exclude") or qp.getlist("exclude"),
        "default_view": form.get("view", qp.get("view", "list")),
        "selected_users": selected if selected else "all",
    }
    repo.update_user(user["email"], {"preferences": prefs})
    return Response(status_code=204)
