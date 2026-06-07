"""Game comparison routes: main page, table fragment, job polling, refresh."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from gamatrix.auth.dependencies import (
    current_user,
    current_user_api,
    get_repo,
    require_admin,
)
from gamatrix.config import get_settings
from gamatrix.constants import (
    ENRICHMENT_NOT_FOUND,
    ENRICHMENT_PENDING,
    JOB_COMPLETED,
    JOB_FAILED,
)
from gamatrix.games import service
from gamatrix.games.preferences import merge_preferences
from gamatrix.games.service import CompareOptions
from gamatrix.helpers import parse_iso
from gamatrix.jobs import create_enrichment_job
from gamatrix.storage.dynamo import Repository
from gamatrix.storage.queue import get_queue
from gamatrix.templating import templates
from datetime import datetime, timedelta, timezone

router = APIRouter(tags=["games"])


def _parse_options(request: Request, user: dict) -> CompareOptions:
    """Build CompareOptions from saved preferences overlaid with query params."""
    prefs = merge_preferences(user.get("preferences", {}))
    qp = request.query_params

    # User selection: explicit ?user=… params win; otherwise saved preference.
    selected: list[str] = qp.getlist("user")
    if not selected:
        pref_users = prefs["selected_users"]
        if pref_users == "all":
            selected = [
                str(u["user_id"]) for u in get_repo().scan_users() if u.get("user_id")
            ]
        else:
            selected = list(pref_users)

    def flag(name: str, default: bool) -> bool:
        if name in qp:
            return qp[name] in ("true", "on", "1")
        return default

    view = qp.get("view", prefs["default_view"])
    return CompareOptions(
        selected_user_ids=selected,
        include_single_player=flag("single_player", prefs["include_single_player"]),
        installed_only=flag("installed_only", prefs["installed_only"]),
        exclude_platforms=qp.getlist("exclude") or prefs["exclude_platforms"],
        exclusive=flag("exclusive", prefs["exclusive"]),
        all_games=view == "grid",
        randomize=flag("randomize", False),
        show_keys=flag("show_keys", prefs["show_keys"]),
        sort=qp.get("sort", "title"),
        direction=qp.get("dir", "asc"),
    )


def _maybe_enrich(repo: Repository, opts: CompareOptions) -> str | None:
    """Find stale/pending games among the selected libraries and enqueue a job.

    If a job is already pending or running, return its id rather than
    creating a duplicate — each /games page load would otherwise queue a
    new batch of the same games, exhausting Lambda concurrency.
    """
    active = repo.get_active_job()
    if active:
        return active["job_id"]

    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.igdb_stale_days)

    release_keys: set[str] = set()
    for user_id in opts.selected_user_ids:
        for entry in repo.get_user_library(user_id):
            release_keys.add(entry["release_key"])

    stale: list[str] = []
    for rk, game in repo.batch_get_games(release_keys).items():
        status = game.get("enrichment_status")
        if status in (ENRICHMENT_PENDING, None):
            stale.append(rk)
            continue
        enriched_at = game.get("enriched_at")
        if enriched_at and parse_iso(enriched_at) < cutoff:
            stale.append(rk)

    if not stale:
        return None
    return create_enrichment_job(repo, get_queue(), stale)


@router.get("/games", response_class=HTMLResponse)
def games_page(
    request: Request,
    user: dict = Depends(current_user),
    repo: Repository = Depends(get_repo),
):
    opts = _parse_options(request, user)
    job_id = _maybe_enrich(repo, opts)
    result = service.compare(repo, opts)
    caption = service.build_caption(repo, opts, result)
    users = {str(u["user_id"]): u for u in repo.scan_users() if u.get("user_id")}
    return templates.TemplateResponse(
        request,
        "games.html.jinja",
        {
            "user": user,
            "users": users,
            "games": result.games,
            "caption": caption,
            "opts": opts,
            "prefs": merge_preferences(user.get("preferences", {})),
            "job_id": job_id,
            "job": repo.get_job(job_id) if job_id else None,
            "is_grid": opts.all_games,
        },
    )


@router.get("/games/table", response_class=HTMLResponse)
def games_table(
    request: Request,
    user: dict = Depends(current_user_api),
    repo: Repository = Depends(get_repo),
):
    opts = _parse_options(request, user)
    result = service.compare(repo, opts)
    caption = service.build_caption(repo, opts, result)
    users = {str(u["user_id"]): u for u in repo.scan_users() if u.get("user_id")}
    return templates.TemplateResponse(
        request,
        "games_table.html.jinja",
        {
            "users": users,
            "games": result.games,
            "caption": caption,
            "opts": opts,
            "is_grid": opts.all_games,
        },
    )


@router.get("/api/jobs/{job_id}", response_class=HTMLResponse)
def job_status(
    request: Request,
    job_id: str,
    user: dict = Depends(current_user_api),
    repo: Repository = Depends(get_repo),
):
    job = repo.get_job(job_id)
    done = job is None or job["status"] in (JOB_COMPLETED, JOB_FAILED)
    return templates.TemplateResponse(
        request,
        "job_status.html.jinja",
        {"job": job, "job_id": job_id, "done": done},
    )


@router.post("/games/refresh-igdb", response_class=HTMLResponse)
def refresh_missing(
    request: Request,
    admin: dict = Depends(require_admin),
    repo: Repository = Depends(get_repo),
):
    """Re-enrich games IGDB didn't find last time."""
    release_keys = [
        g["release_key"]
        for g in repo.scan_all_games()
        if g.get("enrichment_status") == ENRICHMENT_NOT_FOUND
    ]
    job_id = create_enrichment_job(repo, get_queue(), release_keys)
    return templates.TemplateResponse(
        request,
        "job_status.html.jinja",
        {
            "job": repo.get_job(job_id) if job_id else None,
            "job_id": job_id,
            "done": job_id is None,
        },
    )


@router.post("/games/refresh-igdb-all", response_class=HTMLResponse)
def refresh_all(
    request: Request,
    admin: dict = Depends(require_admin),
    repo: Repository = Depends(get_repo),
):
    """Re-enrich every game from scratch."""
    release_keys = [g["release_key"] for g in repo.scan_all_games()]
    # Mark them pending so the staleness check and UI reflect the refresh.
    for rk in release_keys:
        game = repo.get_game(rk)
        if game:
            repo.put_game({**game, "enrichment_status": ENRICHMENT_PENDING})
    job_id = create_enrichment_job(repo, get_queue(), release_keys)
    return templates.TemplateResponse(
        request,
        "job_status.html.jinja",
        {
            "job": repo.get_job(job_id) if job_id else None,
            "job_id": job_id,
            "done": job_id is None,
        },
    )
