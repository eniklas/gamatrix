"""Stable public and deployment-selected authenticated Jinja environments."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.responses import Response

from gamatrix import __version__
from gamatrix.config import get_settings
from gamatrix.constants import PLATFORMS
from gamatrix.games.preferences import merge_preferences
from gamatrix.helpers import pic_url

TEMPLATES_DIR = Path(__file__).parent / "templates"
AUTHENTICATED_TEMPLATE_NAMES = (
    "base.html.jinja",
    "games.html.jinja",
    "games_table.html.jinja",
    "job_status.html.jinja",
    "passkeys.html.jinja",
    "passkeys_list.html.jinja",
    "preferences.html.jinja",
    "tokens.html.jinja",
    "tokens_list.html.jinja",
    "upload.html.jinja",
)
UX_TEMPLATES = ("default", "modern")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _configure(environment: Jinja2Templates) -> Jinja2Templates:
    environment.env.globals["version"] = __version__
    environment.env.globals["platforms"] = list(PLATFORMS)
    environment.env.globals["pic_url"] = pic_url
    return environment


_configure(templates)


def build_authenticated_templates(template_name: str) -> Jinja2Templates:
    """Build an environment for a validated deployment-selected UX template."""
    if template_name not in UX_TEMPLATES:
        raise ValueError(f"Unknown UX template: {template_name}")
    return _configure(Jinja2Templates(directory=str(TEMPLATES_DIR / template_name)))


authenticated_templates = build_authenticated_templates(get_settings().ux_template)


def authenticated_template(
    request,
    name: str,
    context: dict,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    media_type: str | None = None,
    background: BackgroundTask | None = None,
) -> Response:
    """Render an authenticated page or fragment with the account's saved mode."""
    user = context.get("user") or {}
    mode = merge_preferences(user.get("preferences", {})).get("display_mode")
    return authenticated_templates.TemplateResponse(
        request,
        name,
        {**context, "display_mode": mode},
        status_code=status_code,
        headers=headers,
        media_type=media_type,
        background=background,
    )


def authenticated_fragment(
    request,
    name: str,
    context: dict,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    media_type: str | None = None,
    background: BackgroundTask | None = None,
) -> Response:
    """Render an authenticated fragment without base-layout display-mode wiring."""
    return authenticated_templates.TemplateResponse(
        request,
        name,
        context,
        status_code=status_code,
        headers=headers,
        media_type=media_type,
        background=background,
    )
