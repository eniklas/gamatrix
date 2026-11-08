"""Interaction regressions exercised with the app and real HTMX in Chromium."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from gamatrix.app import app
from gamatrix.auth.dependencies import current_user, current_user_api, get_repo
from gamatrix.games import routes
from gamatrix.templating import build_authenticated_templates


@pytest.fixture(params=["default", "modern"])
def comment_page(request, repo, monkeypatch):
    user = {"email": "viewer@example.com", "username": "Viewer", "user_id": "1"}
    repo.put_user(user)
    for key, slug, title in [
        ("steam_1", "alpha", "Alpha"),
        ("gog_2", "alpha", "Alpha"),
        ("steam_3", "beta", "Beta"),
    ]:
        repo.put_game(
            {
                "release_key": key,
                "slug": slug,
                "title": title,
                "multiplayer": True,
                "max_players": 4,
                "rating": 80 if slug == "alpha" else 90,
            }
        )
    repo.replace_user_library(
        "1",
        [
            {"release_key": "steam_1", "platform": "steam"},
            {"release_key": "steam_3", "platform": "steam"},
        ],
    )
    repo.put_user({"email": "other@example.com", "username": "Other", "user_id": "2"})
    repo.replace_user_library("2", [{"release_key": "gog_2", "platform": "gog"}])
    for slug in ("alpha", "beta"):
        repo.put_metadata({"slug": slug, "comment": f"Original {slug}"})
    environment = build_authenticated_templates(request.param)
    monkeypatch.setattr(
        "gamatrix.templating.get_authenticated_templates", lambda: environment
    )
    monkeypatch.setattr(routes.service, "ensure_enrichment_job", lambda *args: None)
    monkeypatch.setitem(app.dependency_overrides, get_repo, lambda: repo)
    monkeypatch.setitem(app.dependency_overrides, current_user, lambda: user)
    monkeypatch.setitem(app.dependency_overrides, current_user_api, lambda: user)
    with sync_playwright() as playwright:
        executable = os.environ.get("GAMATRIX_TEST_BROWSER")
        if not executable and not os.path.exists(playwright.chromium.executable_path):
            pytest.skip(
                "Browser tests require `uv run playwright install chromium` "
                "or GAMATRIX_TEST_BROWSER pointing to Chrome."
            )
        browser = playwright.chromium.launch(executable_path=executable)
        with TestClient(app) as client:
            page = browser.new_page(timezone_id="America/Los_Angeles")

            def dispatch(route):
                url = urlsplit(route.request.url)
                response = client.request(
                    route.request.method,
                    url.path + (f"?{url.query}" if url.query else ""),
                    content=route.request.post_data_buffer,
                    headers=route.request.headers,
                )
                headers = dict(response.headers)
                headers.pop("content-encoding", None)
                headers.pop("content-length", None)
                route.fulfill(
                    status=response.status_code,
                    headers=headers,
                    body=response.content,
                )

            page.route("http://testserver/**", dispatch)
            page.goto("http://testserver/games?view=grid&user=1&user=2")
            page.wait_for_function("typeof htmx !== 'undefined'")
            yield page, repo
        browser.close()


def test_comment_validation_error_is_visible(comment_page):
    page, repo = comment_page
    cell = page.locator(".game-comment-cell").first
    cell.locator(".comment-edit").click()
    textarea = cell.locator("textarea")
    textarea.wait_for()
    textarea.fill("   ")
    cell.get_by_role("button", name="Save", exact=True).click()
    cell.locator(".error").wait_for(timeout=3000)
    assert "cannot be empty" in cell.locator(".error").inner_text()
    assert textarea.input_value() == ""
    assert repo.get_metadata("alpha")["comment"] == "Original alpha"
