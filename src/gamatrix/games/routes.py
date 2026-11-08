"""Game comparison routes: main page, table fragment, job polling, refresh."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
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
    GAME_COMMENT_MAX_LENGTH,
    JOB_COMPLETED,
    JOB_FAILED,
)
from gamatrix.games import api, service, web
from gamatrix.games.preferences import merge_preferences
from gamatrix.helpers import comment_author
from gamatrix.jobs import create_enrichment_job, is_job_stale
from gamatrix.storage.dynamo import Repository
from gamatrix.storage.queue import get_queue
from gamatrix.templating import authenticated_fragment, authenticated_template

router = APIRouter(tags=["games"])


def _comment_game(repo: Repository, release_key: str) -> dict:
    game = repo.get_game(release_key)
    if not game or not game.get("slug"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Game not found",
        )
    override = repo.get_metadata(game["slug"]) or {}
    return {
        "release_key": release_key,
        "title": game.get("title", release_key),
        "slug": game["slug"],
        "comment": override.get("comment", ""),
        "comment_updated_at": override.get("comment_updated_at"),
        "comment_updated_by": comment_author(override, repo.scan_users()),
        "comment_revision": override.get("comment_revision", 0),
    }


def _comment_fragment(
    request: Request,
    game: dict,
    *,
    editing: bool = False,
    error: str | None = None,
    stored_comment: str | None = None,
    status_code: int = 200,
    changed: bool = False,
):
    return authenticated_fragment(
        request,
        "game_comment.html.jinja",
        {
            "game": game,
            "editing": editing,
            "error": error,
            "stored_comment": stored_comment,
        },
        status_code=status_code,
        headers=(
            {
                "HX-Trigger-After-Swap": json.dumps(
                    {
                        "commentChanged": {
                            "slug": game["slug"],
                            "releaseKey": game["release_key"],
                        }
                    }
                )
            }
            if changed
            else None
        ),
    )


@router.get("/games", response_class=HTMLResponse)
def games_page(
    request: Request,
    user: dict = Depends(current_user),
    repo: Repository = Depends(get_repo),
):
    opts = web.parse_options(request, user, repo)
    # Reuse an active enrichment job rather than queueing duplicate work on
    # every page load for the same stale selection.
    job_id = service.ensure_enrichment_job(repo, get_queue(), opts.to_query())
    users = {str(u["user_id"]): u for u in repo.scan_users() if u.get("user_id")}
    result = service.compare(repo, opts.to_query())
    caption = web.build_caption(users, opts, result)
    return authenticated_template(
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
    return authenticated_fragment(
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


@router.get("/games/{release_key}/comment", response_class=HTMLResponse)
def game_comment(
    release_key: str,
    request: Request,
    user: dict = Depends(current_user_api),
    repo: Repository = Depends(get_repo),
):
    return _comment_fragment(request, _comment_game(repo, release_key))


@router.get("/games/{release_key}/comment/edit", response_class=HTMLResponse)
def edit_game_comment(
    release_key: str,
    request: Request,
    user: dict = Depends(current_user_api),
    repo: Repository = Depends(get_repo),
):
    return _comment_fragment(
        request,
        _comment_game(repo, release_key),
        editing=True,
    )


@router.post("/games/{release_key}/comment", response_class=HTMLResponse)
def save_game_comment(
    release_key: str,
    request: Request,
    comment: str = Form(""),
    expected_revision: int = Form(...),
    user: dict = Depends(current_user_api),
    repo: Repository = Depends(get_repo),
):
    game = _comment_game(repo, release_key)
    comment = comment.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not comment:
        return _comment_fragment(
            request,
            {**game, "comment": comment},
            editing=True,
            error="Comment cannot be empty. Use Clear to remove an existing comment.",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    # HTML maxlength counts UTF-16 units, not Python Unicode code points.
    if len(comment.encode("utf-16-le")) // 2 > GAME_COMMENT_MAX_LENGTH:
        return _comment_fragment(
            request,
            {**game, "comment": comment},
            editing=True,
            error=(f"Comment must be {GAME_COMMENT_MAX_LENGTH} characters or fewer."),
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    updated_by = user.get("username") or "anonymous coward"
    if not repo.update_metadata_comment(
        game["slug"],
        comment,
        updated_by,
        expected_revision,
        updated_user_id=user.get("user_id"),
    ):
        current = _comment_game(repo, release_key)
        return _comment_fragment(
            request,
            {**current, "comment": comment},
            editing=True,
            error=(
                "This comment changed while you were editing. "
                "Review the latest stored comment below. "
                "Saving again will replace it with your draft."
            ),
            stored_comment=current["comment"],
            status_code=status.HTTP_409_CONFLICT,
        )
    return _comment_fragment(
        request,
        _comment_game(repo, release_key),
        changed=True,
    )


@router.post("/games/{release_key}/comment/clear", response_class=HTMLResponse)
def clear_game_comment(
    release_key: str,
    request: Request,
    expected_revision: int = Form(...),
    user: dict = Depends(current_user_api),
    repo: Repository = Depends(get_repo),
):
    game = _comment_game(repo, release_key)
    updated_by = user.get("username") or "anonymous coward"
    if not repo.update_metadata_comment(
        game["slug"],
        None,
        updated_by,
        expected_revision,
        updated_user_id=user.get("user_id"),
    ):
        current = _comment_game(repo, release_key)
        return _comment_fragment(
            request,
            current,
            editing=True,
            error=(
                "This comment changed while you were editing. "
                "Review the latest stored comment below before trying again."
            ),
            stored_comment=current["comment"],
            status_code=status.HTTP_409_CONFLICT,
        )
    return _comment_fragment(
        request,
        _comment_game(repo, release_key),
        changed=True,
    )


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
    return authenticated_fragment(
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
    return authenticated_fragment(
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
    games = repo.scan_all_games()
    # Flip every game to pending (batched) so the enricher won't skip it and the
    # staleness check / UI reflect the refresh. The scan already returned each
    # full record, so re-fetching per row would just be wasted round-trips.
    repo.mark_games_pending(games)
    release_keys = [g["release_key"] for g in games]
    job_id = create_enrichment_job(repo, get_queue(), release_keys)
    return authenticated_fragment(
        request,
        "job_status.html.jinja",
        {
            "job": repo.get_job(job_id) if job_id else None,
            "job_id": job_id,
            "done": job_id is None,
        },
    )
