"""Template-level guards for the shared base layout."""

from __future__ import annotations

import re
import types
from pathlib import Path

from gamatrix.templating import (
    AUTHENTICATED_TEMPLATE_NAMES,
    build_authenticated_templates,
    templates,
)
from gamatrix.ux_templates import UX_TEMPLATES
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


def test_base_templates_link_to_github_feedback_forms():
    expected_links = (
        "https://github.com/eniklas/gamatrix/issues/new?template=bug_report.yml",
        "https://github.com/eniklas/gamatrix/issues/new?template=feature_request.yml",
    )
    base_html = templates.env.get_template("base.html.jinja").render()
    for link in expected_links:
        assert f'href="{link}"' in base_html

    for ux_template in UX_TEMPLATES:
        environment = build_authenticated_templates(ux_template)
        html = environment.env.get_template("base.html.jinja").render()
        for link in expected_links:
            assert f'href="{link}"' in html


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


def test_authenticated_stylesheets_style_passkey_delete_confirmation():
    for ux_template in UX_TEMPLATES:
        css = Path(STATIC_TEMPLATES_DIR / ux_template / "style.css").read_text()
        assert ".passkey-delete-confirm" in css
        assert ".passkey-delete-confirm form" in css
        assert ".passkey-delete-confirm input[type=password]" in css


def test_game_comments_are_editable_and_safely_linkified():
    game = {
        "release_key": "steam_10",
        "title": "Coop Game",
        "comment": "Read https://example.com/mod\n<script>alert(1)</script>",
        "comment_updated_at": "2026-11-07T05:00:00+00:00",
        "comment_updated_by": "Alice",
        "comment_revision": 2,
    }
    for ux_template in UX_TEMPLATES:
        environment = build_authenticated_templates(ux_template)
        display = environment.env.get_template("game_comment.html.jinja").render(
            game=game,
            editing=False,
            error=None,
        )
        edit = environment.env.get_template("game_comment.html.jinja").render(
            game=game,
            editing=True,
            error=None,
        )
        assert 'href="https://example.com/mod"' in display
        assert "<script>" not in display
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in display
        assert 'title="Alice edited 2026-11-07 05:00"' in display
        assert 'class="game-comment-details"' in display
        assert 'class="game-comment-preview"' in display
        assert 'class="game-comment-popup"' in display
        assert "/games/steam_10/comment/edit" in display
        assert 'aria-label="add/edit comment"' in display
        assert 'title="add/edit comment"' in display
        assert "📝" in display
        assert "<textarea" in edit
        assert 'maxlength="512"' in edit
        assert 'name="expected_revision" value="2"' in edit
        assert '<button type="submit" class="btn">Save</button>' in edit
        assert 'class="btn secondary"' in edit
        assert ">Cancel</button>" in edit
        assert ">Clear</button>" in edit
        assert "/games/steam_10/comment/clear" in edit


def test_comment_styles_and_overflow_script_keep_rows_compact():
    script = Path("src/gamatrix/static/comment_popup.js").read_text()
    assert "preview.scrollWidth > preview.clientWidth" in script
    assert 'classList.toggle("is-overflowing", overflowing)' in script

    for ux_template in UX_TEMPLATES:
        css = Path(STATIC_TEMPLATES_DIR / ux_template / "style.css").read_text()
        assert (
            ".comment-column, .game-comment-cell { width: 20%; max-width: 20%; }" in css
        )
        assert "white-space: nowrap" in css
        assert "text-overflow: ellipsis" in css
        assert ".game-comment-popup" in css
        assert ".comment-actions .btn { flex: 0 0 auto; white-space: nowrap; }" in css


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
