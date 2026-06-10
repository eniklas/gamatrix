"""Authentication routes: login, logout, and password reset."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from gamatrix.auth import passkeys, service
from gamatrix.auth.dependencies import current_user, current_user_api, get_repo
from gamatrix.auth.service import COOKIE_NAME
from gamatrix.config import get_settings
from gamatrix.storage.dynamo import Repository
from gamatrix.templating import templates

router = APIRouter(prefix="/auth", tags=["auth"])


class RegistrationOptionsRequest(BaseModel):
    password: str
    friendly_name: str


class CeremonyVerificationRequest(BaseModel):
    challenge_id: str
    credential: dict


class DeletePasskeyRequest(BaseModel):
    password: str


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


def _passkey_error(exc: passkeys.PasskeyError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _passkey_credentials(user: dict, repo: Repository) -> list[dict]:
    user_handle = user.get("webauthn_user_id")
    return repo.list_passkeys(user_handle) if user_handle else []


@router.get("/passkeys", response_class=HTMLResponse)
def passkey_management(
    request: Request,
    user: dict = Depends(current_user),
    repo: Repository = Depends(get_repo),
):
    return templates.TemplateResponse(
        request,
        "passkeys.html.jinja",
        {"user": user, "passkeys": _passkey_credentials(user, repo)},
    )


@router.get("/passkeys/list", response_class=HTMLResponse)
def list_passkeys(
    request: Request,
    user: dict = Depends(current_user),
    repo: Repository = Depends(get_repo),
):
    return templates.TemplateResponse(
        request,
        "passkeys_list.html.jinja",
        {"passkeys": _passkey_credentials(user, repo)},
    )


@router.post("/passkeys/register/options")
def passkey_registration_options(
    body: RegistrationOptionsRequest,
    user: dict = Depends(current_user_api),
    repo: Repository = Depends(get_repo),
):
    if not service.authenticate(repo, user["email"], body.password):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Wrong password."
        )
    try:
        return passkeys.registration_options(repo, user, body.friendly_name)
    except passkeys.PasskeyError as exc:
        raise _passkey_error(exc) from exc


@router.post("/passkeys/register/verify")
def passkey_registration_verify(
    body: CeremonyVerificationRequest,
    user: dict = Depends(current_user_api),
    repo: Repository = Depends(get_repo),
):
    try:
        return passkeys.verify_registration(
            repo, user, body.challenge_id, body.credential
        )
    except passkeys.PasskeyError as exc:
        raise _passkey_error(exc) from exc


@router.post("/passkeys/authenticate/options")
def passkey_authentication_options(repo: Repository = Depends(get_repo)):
    return passkeys.authentication_options(repo)


@router.post("/passkeys/authenticate/verify")
def passkey_authentication_verify(
    body: CeremonyVerificationRequest,
    repo: Repository = Depends(get_repo),
):
    try:
        user = passkeys.verify_authentication(repo, body.challenge_id, body.credential)
    except passkeys.PasskeyError as exc:
        raise _passkey_error(exc) from exc
    response = JSONResponse({"redirect": "/games"})
    response.set_cookie(
        value=service.create_session_token(user["email"]), **_cookie_kwargs()
    )
    return response


@router.delete("/passkeys/{credential_id}")
def delete_passkey(
    credential_id: str,
    body: DeletePasskeyRequest,
    user: dict = Depends(current_user_api),
    repo: Repository = Depends(get_repo),
):
    if not service.authenticate(repo, user["email"], body.password):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Wrong password."
        )
    user_handle = user.get("webauthn_user_id")
    if not user_handle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No passkeys are registered for this account.",
        )
    if not repo.delete_passkey(credential_id, user_handle):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Passkey not found for this account.",
        )
    return {"deleted": True}


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
