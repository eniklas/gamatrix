"""FastAPI dependencies for the current user and admin gating."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from gamatrix.auth.service import COOKIE_NAME, decode_session_token
from gamatrix.storage.dynamo import Repository, get_repository


class RedirectToLogin(Exception):
    """Raised when an unauthenticated user hits a page route."""


def get_repo() -> Repository:
    return get_repository()


def current_user(request: Request, repo: Repository = Depends(get_repo)) -> dict:
    """Return the logged-in user, or raise RedirectToLogin for page routes."""
    token = request.cookies.get(COOKIE_NAME)
    email = decode_session_token(token) if token else None
    user = repo.get_user(email) if email else None
    if user is None:
        raise RedirectToLogin()
    return user


def current_user_api(request: Request, repo: Repository = Depends(get_repo)) -> dict:
    """Like current_user but returns 401 instead of redirecting (for API/HTMX)."""
    token = request.cookies.get(COOKIE_NAME)
    email = decode_session_token(token) if token else None
    user = repo.get_user(email) if email else None
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return user


def current_user_upload(request: Request, repo: Repository = Depends(get_repo)) -> dict:
    """Authenticate an upload either by session cookie (browser) or by a personal
    API token (unattended scripts). Returns 401 if neither identifies a user.

    The token path is what restores v1's scriptable `curl` uploads (issue #129);
    it is attached only to the upload routes, so a token can't substitute for a
    session cookie elsewhere in the app."""
    # Imported here, not at module scope: gamatrix.auth.tokens imports the
    # Repository from storage, and pulling it in at import time would widen this
    # module's import surface for a path most requests never take.
    from gamatrix.auth.tokens import resolve_token

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        user = resolve_token(repo, auth_header[len("Bearer ") :].strip())
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        return user
    return current_user_api(request, repo)


def require_admin(user: dict = Depends(current_user_api)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return user


def redirect_to_login() -> RedirectResponse:
    return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
