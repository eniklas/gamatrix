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


def require_admin(user: dict = Depends(current_user_api)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return user


def redirect_to_login() -> RedirectResponse:
    return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
