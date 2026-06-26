"""Stable public and deployment-selected authenticated Jinja environments."""

from __future__ import annotations

from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.responses import Response

from gamatrix import __version__
from gamatrix.constants import PLATFORMS
from gamatrix.games.preferences import merge_preferences
from gamatrix.helpers import pic_url
from gamatrix.ux_templates import (
    TEMPLATES_DIR,
    canonicalize_ux_template_name,
)

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
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
_authenticated_templates: Jinja2Templates | None = None


def _configure(environment: Jinja2Templates) -> Jinja2Templates:
    environment.env.globals["version"] = __version__
    environment.env.globals["platforms"] = list(PLATFORMS)
    environment.env.globals["pic_url"] = pic_url
    return environment


_configure(templates)


def build_authenticated_templates(template_name: str) -> Jinja2Templates:
    """Build an environment for a validated deployment-selected UX template.

    The mode directory is searched first so a mode can override any template,
    with the shared top-level templates directory as a fallback so mode pages
    can ``{% include %}`` shared partials (e.g. ``passkeys_manage_list``).
    """
    template_name = canonicalize_ux_template_name(template_name)
    return _configure(
        Jinja2Templates(
            directory=[str(TEMPLATES_DIR / template_name), str(TEMPLATES_DIR)]
        )
    )


def configure_authenticated_templates(template_name: str) -> Jinja2Templates:
    """Configure the authenticated template environment for the running app."""

    global _authenticated_templates
    _authenticated_templates = build_authenticated_templates(template_name)
    return _authenticated_templates


def get_authenticated_templates() -> Jinja2Templates:
    """Return the authenticated template environment chosen during app startup.

    App initialization must call ``configure_authenticated_templates()`` once
    with the validated runtime ``ux_template`` setting before any authenticated
    render helpers are used.
    """

    if _authenticated_templates is None:
        raise RuntimeError(
            "Authenticated templates are not configured. "
            "Call configure_authenticated_templates() during app initialization."
        )
    return _authenticated_templates


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
    return get_authenticated_templates().TemplateResponse(
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
    return get_authenticated_templates().TemplateResponse(
        request,
        name,
        context,
        status_code=status_code,
        headers=headers,
        media_type=media_type,
        background=background,
    )
