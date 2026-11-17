"""Admin page for fixing IGDB matches by hand.

The enricher guesses an IGDB slug from each title, but IGDB's real slugs aren't
derivable (``foo--1``, dropped subtitles), so some games come back not found or
matched to the wrong entry. An admin can pin the right IGDB slug here; it's
stored on the title's ``metadata_overrides`` row (keyed by our slug) so uploads
and refreshes never revert it, and saving re-enriches just that title.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse

from gamatrix.auth.dependencies import get_repo, require_admin, require_admin_page
from gamatrix.constants import ENRICHMENT_NOT_FOUND, JOB_COMPLETED, JOB_FAILED
from gamatrix.helpers import is_igdb_slug
from gamatrix.jobs import create_enrichment_job, is_job_stale
from gamatrix.storage.dynamo import Repository
from gamatrix.storage.queue import get_queue
from gamatrix.templating import authenticated_fragment, authenticated_template

router = APIRouter(prefix="/admin/igdb", tags=["admin"])

IGDB_GAMES_HOSTS = {"igdb.com", "www.igdb.com"}


@dataclass
class IgdbRow:
    """One title (all platform copies sharing our slug) on the admin page."""

    slug: str
    title: str
    release_keys: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    status: str | None = None
    igdb_id: int = 0
    igdb_name: str | None = None
    igdb_matched_slug: str | None = None
    igdb_slug: str | None = None


def normalize_igdb_slug(value: str) -> str | None:
    """Turn admin input (a bare slug or a pasted IGDB game URL) into a slug.

    Returns None when the input isn't a plausible IGDB slug. The IGDB client
    checks again before querying, since a slug can also be set in DynamoDB.
    """
    value = value.strip()
    if "/" in value:
        parsed = urlparse(value if "://" in value else f"https://{value}")
        parts = [p for p in parsed.path.split("/") if p]
        if (
            (parsed.hostname or "").lower() not in IGDB_GAMES_HOSTS
            or len(parts) < 2
            or parts[0] != "games"
        ):
            return None
        value = parts[1]
    value = value.lower()
    return value if is_igdb_slug(value) else None


def _rows(repo: Repository, q: str = "", fresh: bool = False) -> list[IgdbRow]:
    """Titles to show: by default the misses plus any with a manual slug; with
    a search term, every title whose name contains it.

    Built from the repository's cached read model (the same one the games page
    uses), so the live search filters in memory rather than scanning the
    games table per keystroke. `fresh` re-reads it, for the reload after a
    re-enrichment job, whose writes come from another process.
    """
    overrides = repo.get_all_metadata()
    by_slug: dict[str, IgdbRow] = {}
    for game in repo.get_all_games_map(fresh=fresh).values():
        slug = game.get("slug")
        if not slug:
            continue
        row = by_slug.get(slug)
        if row is None:
            row = by_slug[slug] = IgdbRow(
                slug=slug,
                title=game.get("title", slug),
                igdb_slug=overrides.get(slug, {}).get("igdb_slug"),
            )
        row.release_keys.append(game["release_key"])
        platform = game.get("platform") or game["release_key"].split("_")[0]
        if platform not in row.platforms:
            row.platforms.append(platform)
        # Copies sharing a slug can disagree (one found by uid, one not);
        # surface the miss, since that's what the page exists to fix.
        if row.status != ENRICHMENT_NOT_FOUND:
            row.status = game.get("enrichment_status")
        if game.get("igdb_id") and not row.igdb_id:
            row.igdb_id = game["igdb_id"]
            row.igdb_name = game.get("igdb_name")
            row.igdb_matched_slug = game.get("igdb_matched_slug")

    needle = q.strip().casefold()
    if needle:
        rows = [r for r in by_slug.values() if needle in r.title.casefold()]
    else:
        rows = [
            r
            for r in by_slug.values()
            if r.status == ENRICHMENT_NOT_FOUND or r.igdb_slug
        ]
    return sorted(rows, key=lambda r: r.title.casefold())


def _list_fragment(
    request: Request,
    repo: Repository,
    q: str,
    *,
    job_id: str | None = None,
    message: str | None = None,
    error: str | None = None,
    error_slug: str | None = None,
    draft: str | None = None,
    fresh: bool = False,
    status_code: int = 200,
):
    return authenticated_fragment(
        request,
        "admin_igdb_list.html.jinja",
        {
            "rows": _rows(repo, q, fresh),
            "q": q,
            "job_id": job_id,
            "job": repo.get_job(job_id) if job_id else None,
            "done": False,
            "failed": False,
            "message": message,
            "error": error,
            "error_slug": error_slug,
            "draft": draft,
        },
        status_code=status_code,
    )


def _reenrich(repo: Repository, slug: str) -> str | None:
    """Mark every copy of a title pending and queue a job for just those.

    "Every copy" includes copies under a different title that Galaxy links to
    the same game (a shared igdb_key): the enricher applies a manual slug to
    that whole group, so they must be re-fetched together.

    The cached read model is enough to find the release keys, but the rows
    themselves are re-read before being written back as pending, so a stale
    cached copy can't overwrite newer data from another process.
    """
    games = repo.get_all_games_map().values()
    igdb_keys = {g["igdb_key"] for g in games if g.get("slug") == slug}
    keys = [
        g["release_key"]
        for g in games
        if g.get("slug") == slug or g.get("igdb_key") in igdb_keys
    ]
    fresh = list(repo.batch_get_games(keys).values())
    repo.mark_games_pending(fresh)
    return create_enrichment_job(repo, get_queue(), [g["release_key"] for g in fresh])


@router.get("", response_class=HTMLResponse)
def igdb_page(
    request: Request,
    q: str = "",
    admin: dict = Depends(require_admin_page),
    repo: Repository = Depends(get_repo),
):
    return authenticated_template(
        request,
        "admin_igdb.html.jinja",
        {
            "user": admin,
            "rows": _rows(repo, q),
            "q": q,
            "job_id": None,
            "job": None,
            "done": False,
            "failed": False,
        },
    )


@router.get("/list", response_class=HTMLResponse)
def igdb_list(
    request: Request,
    q: str = "",
    fresh: bool = False,
    admin: dict = Depends(require_admin),
    repo: Repository = Depends(get_repo),
):
    return _list_fragment(request, repo, q, fresh=fresh)


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def igdb_job(
    request: Request,
    job_id: str,
    admin: dict = Depends(require_admin),
    repo: Repository = Depends(get_repo),
):
    """Progress for a slug fix's re-enrichment.

    On success it reloads the list. A failed or stalled job stops polling and
    says so instead, leaving the admin to reload, so a failure can't vanish
    behind a list that looks like it updated.
    """
    job = repo.get_job(job_id)
    failed = job is not None and (job.get("status") == JOB_FAILED or is_job_stale(job))
    done = job is None or job.get("status") == JOB_COMPLETED
    return authenticated_fragment(
        request,
        "admin_igdb_job.html.jinja",
        {"job": job, "job_id": job_id, "done": done, "failed": failed},
    )


@router.post("/{slug}", response_class=HTMLResponse)
def set_igdb_slug(
    request: Request,
    slug: str,
    igdb_slug: str = Form(""),
    q: str = Form(""),
    admin: dict = Depends(require_admin),
    repo: Repository = Depends(get_repo),
):
    normalized = normalize_igdb_slug(igdb_slug)
    if normalized is None:
        return _list_fragment(
            request,
            repo,
            q,
            error=(
                "Enter an IGDB slug (like hollow-knight) or a link to the game "
                "on igdb.com."
            ),
            error_slug=slug,
            draft=igdb_slug,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    repo.set_metadata_igdb_slug(slug, normalized)
    job_id = _reenrich(repo, slug)
    return _list_fragment(
        request,
        repo,
        q,
        job_id=job_id,
        message=f"Set IGDB slug to {normalized}; re-fetching from IGDB.",
    )


@router.post("/{slug}/clear", response_class=HTMLResponse)
def clear_igdb_slug(
    request: Request,
    slug: str,
    q: str = Form(""),
    admin: dict = Depends(require_admin),
    repo: Repository = Depends(get_repo),
):
    repo.set_metadata_igdb_slug(slug, None)
    job_id = _reenrich(repo, slug)
    return _list_fragment(
        request,
        repo,
        q,
        job_id=job_id,
        message="Cleared the IGDB slug; re-fetching with automatic matching.",
    )
