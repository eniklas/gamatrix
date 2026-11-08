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


def test_comment_conflict_displays_stored_text_and_preserves_draft(comment_page):
    page, repo = comment_page
    cell = page.locator(".game-comment-cell").first
    cell.locator(".comment-edit").click()
    cell.locator("textarea").fill("My draft")
    assert repo.update_metadata_comment("alpha", "Someone else's change", "Other", 0)
    cell.get_by_role("button", name="Save", exact=True).click()
    cell.locator(".error").wait_for(timeout=3000)
    assert cell.locator("textarea").input_value() == "My draft"
    assert "Someone else's change" in cell.inner_text()
    assert repo.get_metadata("alpha")["comment"] == "Someone else's change"
    cell.get_by_role("button", name="Save", exact=True).click()
    cell.locator(".game-comment-display").wait_for()
    assert repo.get_metadata("alpha")["comment"] == "My draft"


def test_comment_save_preserves_sort_and_other_drafts_and_updates_duplicates(
    comment_page,
):
    page, _ = comment_page
    requests = []
    page.on("request", lambda request: requests.append(urlsplit(request.url).path))
    rating = page.locator("th[data-sort-col=rating]")
    rating.click()
    rating.click()
    beta = page.locator("tbody tr").filter(has_text="Beta")
    beta.locator(".comment-edit").click()
    beta.locator("textarea").fill("Unsubmitted beta draft")
    alpha = page.locator("tbody tr").filter(has_text="Alpha").first
    alpha.locator(".comment-edit").click()
    alpha.locator("textarea").fill("Saved shared comment")
    alpha.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_load_state("networkidle")
    assert "/games/table" not in requests
    assert rating.get_attribute("aria-sort") == "descending"
    assert beta.locator("textarea").input_value() == "Unsubmitted beta draft"
    assert page.locator(".game-comment-preview").all_text_contents() == [
        "Saved shared comment",
        "Saved shared comment",
    ]


def test_comment_save_preserves_same_slug_sibling_editor(comment_page):
    page, _ = comment_page
    alpha = page.locator("tbody tr").filter(has_text="Alpha")
    alpha.nth(1).locator(".comment-edit").click()
    alpha.nth(1).locator("textarea").fill("Sibling draft")
    alpha.nth(0).locator(".comment-edit").click()
    alpha.nth(0).locator("textarea").fill("First save")
    alpha.nth(0).get_by_role("button", name="Save", exact=True).click()
    page.wait_for_load_state("networkidle")
    assert alpha.nth(1).locator("textarea").input_value() == "Sibling draft"


def test_comment_edit_time_is_localized_on_load_and_after_swap(comment_page):
    page, repo = comment_page
    repo.put_metadata(
        {
            "slug": "alpha",
            "comment": "Timestamped",
            "comment_updated_by": "Viewer",
            "comment_updated_at": "2026-11-07T05:00:00+00:00",
        }
    )
    page.reload()
    cell = page.locator(".game-comment-cell").first
    details = cell.locator("details")
    assert "2026-11-06 21:00" in details.get_attribute("title")
    assert "PST" in details.get_attribute("title") or "PDT" in details.get_attribute(
        "title"
    )
    cell.locator(".comment-edit").click()
    cell.locator("textarea").fill("Saved timestamp")
    cell.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_load_state("networkidle")
    for title in page.locator("details").evaluate_all(
        "(elements) => elements.map(el => el.title)"
    ):
        if "edited" in title:
            assert "PST" in title or "PDT" in title
