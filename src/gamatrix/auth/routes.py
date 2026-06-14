"""Authentication routes: login, logout, and password reset."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from gamatrix.auth import passkeys, service, tokens
from gamatrix.auth.dependencies import current_user, current_user_api, get_repo
from gamatrix.auth.service import COOKIE_NAME
from gamatrix.config import get_settings
from gamatrix.constants import API_TOKEN_NAME_MAX_LENGTH
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


class CreateTokenRequest(BaseModel):
    password: str
    name: str


class DeleteTokenRequest(BaseModel):
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


def _token_setup_snippet(token: str) -> str:
    """A ready-to-paste PowerShell block: store the token with locked-down
    permissions, fetch the uploader, and schedule a daily run."""
    base_url = get_settings().app_base_url.rstrip("/")
    return (
        "# 1) Save your token where only your Windows account can read it.\n"
        '$dir = "$env:USERPROFILE\\.gamatrix"\n'
        '$tokenPath = "$dir\\token"\n'
        "New-Item -ItemType Directory -Force $dir | Out-Null\n"
        f"Set-Content $tokenPath '{token}' -NoNewline\n"
        "# Replace the file's ACL outright (no inherited perms) and grant just\n"
        "# you read+write, so you can re-cycle the token later without a fight.\n"
        "$currentUser = "
        "[System.Security.Principal.WindowsIdentity]::GetCurrent().Name\n"
        "$acl = New-Object System.Security.AccessControl.FileSecurity\n"
        "$acl.SetAccessRuleProtection($true, $false)\n"
        "$rule = New-Object System.Security.AccessControl.FileSystemAccessRule("
        "$currentUser, "
        "([System.Security.AccessControl.FileSystemRights]::Read -bor "
        "[System.Security.AccessControl.FileSystemRights]::Write), "
        "[System.Security.AccessControl.AccessControlType]::Allow)\n"
        "$acl.SetAccessRule($rule)\n"
        "Set-Acl -Path $tokenPath -AclObject $acl\n\n"
        "# 2) Download the uploader script.\n"
        f'Invoke-WebRequest "{base_url}/static/upload-gamatrix.ps1" '
        '-OutFile "$dir\\upload-gamatrix.ps1"\n\n'
        "# 3) Schedule a daily upload at 5am (adjust the time as you like).\n"
        "$action = New-ScheduledTaskAction -Execute powershell.exe -Argument "
        f'"-ExecutionPolicy Bypass -File `"$dir\\upload-gamatrix.ps1`" '
        f'-BaseUrl {base_url}"\n'
        "$trigger = New-ScheduledTaskTrigger -Daily -At 5am\n"
        "Register-ScheduledTask -TaskName 'Gamatrix DB upload' -Action $action "
        "-Trigger $trigger\n"
    )


@router.get("/tokens", response_class=HTMLResponse)
def token_management(
    request: Request,
    user: dict = Depends(current_user),
    repo: Repository = Depends(get_repo),
):
    return templates.TemplateResponse(
        request,
        "tokens.html.jinja",
        {
            "user": user,
            "tokens": repo.list_api_tokens(user["email"]),
            "base_url": get_settings().app_base_url.rstrip("/"),
        },
    )


@router.get("/tokens/list", response_class=HTMLResponse)
def list_tokens(
    request: Request,
    user: dict = Depends(current_user),
    repo: Repository = Depends(get_repo),
):
    return templates.TemplateResponse(
        request,
        "tokens_list.html.jinja",
        {"tokens": repo.list_api_tokens(user["email"])},
    )


@router.post("/tokens")
def create_token(
    body: CreateTokenRequest,
    user: dict = Depends(current_user_api),
    repo: Repository = Depends(get_repo),
):
    if not service.authenticate(repo, user["email"], body.password):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Wrong password."
        )
    name = body.name.strip()[:API_TOKEN_NAME_MAX_LENGTH] or "Unnamed token"
    token = tokens.create_api_token(repo, user["email"], name)
    return {"token": token, "snippet": _token_setup_snippet(token)}


@router.delete("/tokens/{token_id}")
def delete_token(
    token_id: str,
    body: DeleteTokenRequest,
    user: dict = Depends(current_user_api),
    repo: Repository = Depends(get_repo),
):
    if not service.authenticate(repo, user["email"], body.password):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Wrong password."
        )
    if not repo.delete_api_token(token_id, user["email"]):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found for this account.",
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
