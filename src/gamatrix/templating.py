"""Shared Jinja2 template environment for the web routes."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from gamatrix import __version__
from gamatrix.constants import PLATFORMS

TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["version"] = __version__
templates.env.globals["platforms"] = list(PLATFORMS)
