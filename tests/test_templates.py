"""Template-level guards for the shared base layout."""

from __future__ import annotations

import re
from pathlib import Path

from gamatrix.games.preferences import DISPLAY_MODES, merge_preferences
from gamatrix.templating import (
    AUTHENTICATED_TEMPLATE_NAMES,
    TEMPLATES_DIR,
    authenticated_templates,
    templates,
)


def test_stylesheet_link_is_cache_busted():
    """The stylesheet must carry a version query so a deploy's CSS changes
    aren't masked by a browser serving a stale /static/style.css."""
    html = templates.env.get_template("base.html.jinja").render()
    assert re.search(r'href="/static/style\.css\?v=[^"]+"', html)


def test_default_authenticated_ux_has_the_complete_template_contract():
    for name in AUTHENTICATED_TEMPLATE_NAMES:
        authenticated_templates.env.get_template(name)


def test_default_authenticated_base_uses_cache_busted_stylesheet():
    html = authenticated_templates.env.get_template("base.html.jinja").render()
    assert "/static/templates/default/style.css?v=" in html


def test_authenticated_templates_apply_only_valid_explicit_modes():
    template = authenticated_templates.env.get_template("base.html.jinja")
    assert 'data-mode="dark"' in template.render(display_mode="dark")
    assert "data-mode" not in template.render(display_mode=None)


def test_default_stylesheet_defines_every_required_mode_and_stays_small():
    path = Path(TEMPLATES_DIR.parent / "static" / "templates" / "default" / "style.css")
    css = path.read_text()
    assert path.stat().st_size < 20_000
    for mode in DISPLAY_MODES:
        assert f'data-mode="{mode}"' in css


def test_default_preferences_template_includes_apply_preview_controls():
    html = authenticated_templates.env.get_template("preferences.html.jinja").render(
        user={"email": "user@x.com", "username": "User", "user_id": "1"},
        users={"1": {"username": "User", "user_id": "1"}},
        prefs=merge_preferences({"display_mode": None}),
    )
    assert '<button type="button" onclick="applyDisplayMode()">Apply</button>' in html
    assert "function applyDisplayMode()" in html
    assert "Save preferences to keep change" in html
    assert "s.textContent='Saved ✓'" in html
