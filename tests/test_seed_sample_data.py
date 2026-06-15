"""Tests for local sample-data seeding and reset behavior."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest


def _load_seed_sample_data(monkeypatch) -> object:
    """Import the script as a top-level module, mirroring CLI execution."""
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    sys.modules.pop("seed_sample_data", None)
    return importlib.import_module("seed_sample_data")


def test_purge_user_account_removes_user_owned_rows(repo):
    repo.put_user(
        {
            "email": "tester@example.com",
            "username": "tester",
            "user_id": "7",
            "webauthn_user_id": "handle-7",
        }
    )
    repo.replace_user_library("7", [{"release_key": "gog_1", "platform": "gog"}])
    repo.put_profile_pic("7", b"\x89PNG")
    assert repo.put_passkey(
        {
            "credential_id": "cred-1",
            "user_handle": "handle-7",
            "friendly_name": "Laptop",
            "public_key": b"pk",
            "sign_count": 0,
            "backed_up": False,
            "created_at": "now",
            "last_used_at": "never",
        }
    )

    repo.purge_user_account(repo.get_user("tester@example.com"))

    assert repo.get_user("tester@example.com") is None
    assert repo.get_user_library("7") == []
    assert repo.get_profile_pic("7") is None
    assert repo.list_passkeys("handle-7") == []


def test_seed_sample_data_refuses_existing_users_without_reset(monkeypatch, tmp_path):
    seed_sample_data = _load_seed_sample_data(monkeypatch)
    manifest = [
        {
            "email": "user1@example.com",
            "username": "user1",
            "user_id": "1",
            "admin": True,
            "fixture": "user1.db",
        }
    ]
    (tmp_path / "seed_manifest.json").write_text(json.dumps(manifest))

    class FakeRepo:
        def scan_users(self) -> list[dict]:
            return [{"email": "existing@example.com", "user_id": "99"}]

    create_calls: list[dict] = []
    ingest_calls: list[tuple] = []

    monkeypatch.setattr(seed_sample_data, "SAMPLE_DIR", tmp_path)
    monkeypatch.setattr(seed_sample_data, "get_repository", lambda: FakeRepo())
    monkeypatch.setattr(seed_sample_data, "get_queue", lambda: object())
    monkeypatch.setattr(
        seed_sample_data,
        "create_user",
        lambda *args, **kwargs: create_calls.append(kwargs),
    )
    monkeypatch.setattr(
        seed_sample_data,
        "ingest_db_file",
        lambda *args, **kwargs: ingest_calls.append(args),
    )
    monkeypatch.setattr(sys, "argv", ["seed_sample_data.py"])

    with pytest.raises(SystemExit) as exc:
        seed_sample_data.main()

    assert "--hard-reset-existing-users" in str(exc.value)
    assert create_calls == []
    assert ingest_calls == []


def test_seed_sample_data_hard_resets_existing_users(monkeypatch, tmp_path):
    seed_sample_data = _load_seed_sample_data(monkeypatch)
    manifest = [
        {
            "email": "user1@example.com",
            "username": "user1",
            "user_id": "1",
            "admin": True,
            "fixture": "user1.db",
        }
    ]
    (tmp_path / "seed_manifest.json").write_text(json.dumps(manifest))

    events: list[tuple[str, str]] = []

    class FakeRepo:
        def scan_users(self) -> list[dict]:
            return [
                {"email": "existing1@example.com", "user_id": "10"},
                {"email": "existing2@example.com", "user_id": "11"},
            ]

        def purge_user_account(self, user: dict) -> None:
            events.append(("purge", user["email"]))

    monkeypatch.setattr(seed_sample_data, "SAMPLE_DIR", tmp_path)
    monkeypatch.setattr(seed_sample_data, "get_repository", lambda: FakeRepo())
    monkeypatch.setattr(seed_sample_data, "get_queue", lambda: object())
    monkeypatch.setattr(
        seed_sample_data,
        "create_user",
        lambda repo, **kwargs: events.append(("create", kwargs["email"])),
    )
    monkeypatch.setattr(
        seed_sample_data,
        "ingest_db_file",
        lambda fixture, repo, queue: (
            events.append(("ingest", fixture)) or ("1", "job-1")
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["seed_sample_data.py", "--hard-reset-existing-users"],
    )

    seed_sample_data.main()

    assert events == [
        ("purge", "existing1@example.com"),
        ("purge", "existing2@example.com"),
        ("create", "user1@example.com"),
        ("ingest", str(tmp_path / "user1.db")),
    ]
