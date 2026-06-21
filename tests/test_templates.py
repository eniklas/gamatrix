"""Template-level guards for the shared base layout."""

from __future__ import annotations

import re
import types
from pathlib import Path

from gamatrix.templating import (
    AUTHENTICATED_TEMPLATE_NAMES,
    UX_TEMPLATES,
    build_authenticated_templates,
    templates,
)
from gamatrix.games.preferences import DISPLAY_MODES
from gamatrix.games.preferences import merge_preferences
from gamatrix.ux_templates import (
    STATIC_TEMPLATES_DIR,
    VALID_TEMPLATE_MARKER,
    discover_ux_templates,
)


def test_stylesheet_link_is_cache_busted():
    """The stylesheet must carry a version query so a deploy's CSS changes
    aren't masked by a browser serving a stale /static/style.css."""
    html = templates.env.get_template("base.html.jinja").render()
    assert re.search(r'href="/static/style\.css\?v=[^"]+"', html)


def test_each_authenticated_ux_has_the_complete_template_contract():
    for ux_template in UX_TEMPLATES:
        environment = build_authenticated_templates(ux_template)
        for name in AUTHENTICATED_TEMPLATE_NAMES:
            environment.env.get_template(name)


def test_authenticated_ux_templates_are_discovered_from_shipped_directories():
    assert UX_TEMPLATES == discover_ux_templates()


def test_discover_ux_templates_requires_valid_template_marker(tmp_path):
    (tmp_path / "marked").mkdir()
    (tmp_path / "marked" / VALID_TEMPLATE_MARKER).write_text("")
    (tmp_path / "unmarked").mkdir()
    (tmp_path / "unmarked" / "base.html.jinja").write_text("")

    assert discover_ux_templates(tmp_path) == ("marked",)


def test_authenticated_bases_use_their_own_cache_busted_stylesheet():
    for ux_template in UX_TEMPLATES:
        environment = build_authenticated_templates(ux_template)
        html = environment.env.get_template("base.html.jinja").render()
        expected = f"/static/templates/{ux_template}/style.css?v="
        assert expected in html


def test_authenticated_templates_apply_only_valid_explicit_modes():
    environment = build_authenticated_templates("modern")
    template = environment.env.get_template("base.html.jinja")
    assert 'data-mode="dark"' in template.render(display_mode="dark")
    assert "data-mode" not in template.render(display_mode=None)


def test_authenticated_templates_accept_case_insensitive_names():
    environment = build_authenticated_templates("MODERN")
    html = environment.env.get_template("base.html.jinja").render()
    assert "/static/templates/modern/style.css?v=" in html


def test_each_stylesheet_defines_every_required_mode_and_stays_small():
    for ux_template in UX_TEMPLATES:
        path = Path(STATIC_TEMPLATES_DIR / ux_template / "style.css")
        css = path.read_text()
        assert path.stat().st_size < 20_000
        for mode in DISPLAY_MODES:
            assert f'data-mode="{mode}"' in css


def test_default_preferences_template_includes_apply_preview_controls():
    environment = build_authenticated_templates("default")
    html = environment.env.get_template("preferences.html.jinja").render(
        user={"email": "user@x.com", "username": "User", "user_id": "1"},
        users={"1": {"username": "User", "user_id": "1"}},
        prefs=merge_preferences({"display_mode": None}),
    )
    assert '<button type="button" onclick="applyDisplayMode()">Apply</button>' in html
    assert "function applyDisplayMode()" in html
    assert "Save preferences to keep change" in html
    assert "s.textContent='Saved ✓'" in html


def test_games_templates_localize_user_db_updated_tooltips():
    opts = types.SimpleNamespace(
        selected_user_ids=["1"],
        include_single_player=False,
        installed_only=False,
        exclusive=False,
        randomize=False,
        show_keys=False,
        exclude_platforms=[],
    )
    users = {
        "1": {
            "username": "User",
            "user_id": "1",
            "db_updated_at": "2026-06-09T04:28:54+00:00",
        },
        "2": {"username": "Never", "user_id": "2"},
    }

    for ux_template in UX_TEMPLATES:
        environment = build_authenticated_templates(ux_template)
        html = environment.env.get_template("games.html.jinja").render(
            user={"email": "user@x.com", "username": "Viewer", "user_id": "9"},
            users=users,
            opts=opts,
            is_grid=False,
            platforms=[],
            job_id=None,
            job=None,
            games=[],
            caption="",
        )
        assert 'class="user-db-updated"' in html
        assert 'data-db-updated="2026-06-09T04:28:54+00:00"' in html
        assert 'title="DB updated: loading local time..."' in html
        assert 'title="DB updated: never"' in html
        assert "function localizeDbUpdatedTooltips()" in html
        assert "when.toLocaleString()" in html


def test_default_token_templates_preserve_management_ui():
    for ux_template in UX_TEMPLATES:
        environment = build_authenticated_templates(ux_template)
        html = environment.env.get_template("tokens.html.jinja").render(
            base_url="https://games.example.com"
        )
        fragment = environment.env.get_template("tokens_list.html.jinja").render(
            tokens=[]
        )
        assert 'id="token-create"' in html
        assert "/auth/tokens/list" in html
        assert "/auth/upload-gamatrix.sh" in html
        assert "No tokens yet." in fragment
