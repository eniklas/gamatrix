"""Authentication routes: login, logout, and password reset."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from gamatrix.auth import service
from gamatrix.auth.dependencies import get_repo
from gamatrix.auth.service import COOKIE_NAME
from gamatrix.config import get_settings
from gamatrix.storage.dynamo import Repository
from gamatrix.templating import templates

router = APIRouter(prefix="/auth", tags=["auth"])


def _cookie_kwargs() -> dict:
    settings = get_settings()
    return {
        "key": COOKIE_NAME,
        "httponly": True,
        "samesite": "lax",
        "secure": not settings.local_dev,
        "max_age": settings.jwt_ttl_hours * 3600,
    }


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html.jinja", {"error": None})


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    repo: Repository = Depends(get_repo),
):
    user = service.authenticate(repo, email, password)
    if user is None:
        return templates.TemplateResponse(
            request,
            "login.html.jinja",
            {"error": "Invalid email or password."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    response = RedirectResponse(url="/games", status_code=status.HTTP_302_FOUND)
    response.set_cookie(value=service.create_session_token(email), **_cookie_kwargs())
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(COOKIE_NAME)
    return response


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_form(request: Request):
    return templates.TemplateResponse(
        request, "forgot_password.html.jinja", {"sent": False}
    )


@router.post("/forgot-password", response_class=HTMLResponse)
def forgot(
    request: Request,
    email: str = Form(...),
    repo: Repository = Depends(get_repo),
):
    service.begin_password_reset(repo, email)
    # Always report success so the form can't be used to enumerate accounts.
    return templates.TemplateResponse(
        request, "forgot_password.html.jinja", {"sent": True}
    )


@router.get("/reset-password", response_class=HTMLResponse)
def reset_form(request: Request, token: str, email: str):
    return templates.TemplateResponse(
        request,
        "reset_password.html.jinja",
        {"token": token, "email": email, "error": None},
    )


@router.post("/reset-password", response_class=HTMLResponse)
def reset(
    request: Request,
    token: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    repo: Repository = Depends(get_repo),
):
    if service.complete_password_reset(repo, email, token, password):
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(
        request,
        "reset_password.html.jinja",
        {
            "token": token,
            "email": email,
            "error": "That reset link is invalid or has expired.",
        },
        status_code=status.HTTP_400_BAD_REQUEST,
    )
