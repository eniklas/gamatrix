"""Game comparison routes: main page, table fragment, job polling, refresh."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from gamatrix.auth.dependencies import (
    current_user,
    current_user_api,
    get_repo,
    require_admin,
)
from gamatrix.constants import (
    ENRICHMENT_NOT_FOUND,
    ENRICHMENT_PENDING,
    JOB_COMPLETED,
    JOB_FAILED,
)
from gamatrix.games import api, service, web
from gamatrix.games.preferences import merge_preferences
from gamatrix.games.web import WebCompareOptions
from gamatrix.jobs import create_enrichment_job, is_job_stale
from gamatrix.storage.dynamo import Repository
from gamatrix.storage.queue import get_queue
from gamatrix.templating import templates

router = APIRouter(tags=["games"])

def _maybe_enrich(repo: Repository, opts: WebCompareOptions) -> str | None:
    """Find stale/pending games among the selected libraries and enqueue a job.

    If a job is already pending or running, return its id rather than
    creating a duplicate — each /games page load would otherwise queue a
    new batch of the same games, exhausting Lambda concurrency.
    """
    advice = service.ensure_enrichment_job(repo, get_queue(), opts.to_query())
    return advice.job_id


@router.get("/games", response_class=HTMLResponse)
def games_page(
    request: Request,
    user: dict = Depends(current_user),
    repo: Repository = Depends(get_repo),
):
    opts = web.parse_options(request, user, repo)
    job_id = _maybe_enrich(repo, opts)
    users = {str(u["user_id"]): u for u in repo.scan_users() if u.get("user_id")}
    result = service.compare(repo, opts.to_query())
    caption = web.build_caption(users, opts, result)
    return templates.TemplateResponse(
        request,
        "games.html.jinja",
        {
            "user": user,
            "users": users,
            "games": web.present_games(result, opts),
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
    users = {str(u["user_id"]): u for u in repo.scan_users() if u.get("user_id")}
    opts = web.parse_options(request, user, repo)
    result = service.compare(repo, opts.to_query())
    caption = web.build_caption(users, opts, result)
    return templates.TemplateResponse(
        request,
        "games_table.html.jinja",
        {
            "users": users,
            "games": web.present_games(result, opts),
            "caption": caption,
            "opts": opts,
            "is_grid": opts.all_games,
        },
    )


@router.get("/api/games")
def games_api(
    request: Request,
    user: dict = Depends(current_user_api),
    repo: Repository = Depends(get_repo),
):
    """Return the comparison dataset as JSON for headless consumers."""
    opts = web.parse_options(request, user, repo)
    query = opts.to_query()
    result = service.compare(repo, query)
    return JSONResponse(api.serialize_comparison(query, result))


@router.get("/api/jobs/{job_id}", response_class=HTMLResponse)
def job_status(
    request: Request,
    job_id: str,
    user: dict = Depends(current_user_api),
    repo: Repository = Depends(get_repo),
):
    job = repo.get_job(job_id)
    done = (
        job is None
        or job.get("status") in (JOB_COMPLETED, JOB_FAILED)
        or is_job_stale(job)
    )
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
    missing = [
        g
        for g in repo.scan_all_games()
        if g.get("enrichment_status") == ENRICHMENT_NOT_FOUND
    ]
    # The enricher only touches games that are unenriched or pending, so flip
    # these from not_found to pending or they'd be skipped (see #134).
    for game in missing:
        repo.put_game({**game, "enrichment_status": ENRICHMENT_PENDING})
    release_keys = [g["release_key"] for g in missing]
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
