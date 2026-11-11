"""Black-box coverage for local development script entrypoints."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from gamatrix.config import Settings
from gamatrix.constants import JOB_COMPLETED, JOB_PENDING
from gamatrix.helpers import now_iso
from gamatrix.storage.dynamo import Repository


def _load_script(monkeypatch, module_name: str) -> object:
    """Import a script module from the repository's scripts directory."""
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def _local_settings(prefix: str) -> Settings:
    """Return test settings suited for local-script integration tests."""
    return Settings(
        table_prefix=prefix,
        jwt_secret="test-secret",
        local_dev=True,
        aws_region="us-east-1",
        dynamodb_endpoint_url=None,
        s3_endpoint_url=None,
        sqs_endpoint_url=None,
        public_s3_endpoint_url=None,
        upload_bucket=f"{prefix}-uploads",
    )


@pytest.mark.parametrize("wipe", [False, True])
def test_metadata_migration_preserves_user_edits_and_clears(
    repo, monkeypatch, tmp_path, wipe
):
    script = _load_script(monkeypatch, "migrate_metadata")
    monkeypatch.setattr("gamatrix.storage.dynamo.get_repository", lambda: repo)
    config = tmp_path / "config.yaml"
    config.write_text(
        "metadata:\n"
        "  Edited:\n    max_players: 8\n    comment: YAML default\n"
        "  Cleared:\n    max_players: 2\n    comment: YAML default\n"
        "  New:\n    comment: Imported default\n"
    )
    repo.put_metadata({"slug": "edited", "url": "https://example.com"})
    assert repo.update_metadata_comment("edited", "User text", "Viewer", 0)
    assert repo.update_metadata_comment("cleared", None, "Viewer", 0)
    repo.put_metadata({"slug": "orphan", "comment": "Keep orphan user text"})
    assert repo.update_metadata_comment("orphan", "Keep orphan user text", "Viewer", 0)
    repo.put_metadata({"slug": "old-config", "max_players": 4})
    before = repo.get_metadata("edited")
    monkeypatch.setattr(
        sys, "argv", ["migrate_metadata.py", str(config)] + (["--wipe"] if wipe else [])
    )
    script.main()
    edited = repo.get_metadata("edited")
    assert edited["comment"] == "User text"
    assert edited["comment_revision"] == before["comment_revision"]
    assert edited["comment_updated_at"] == before["comment_updated_at"]
    assert edited["comment_updated_by"] == before["comment_updated_by"]
    assert edited["max_players"] == 8
    assert "comment" not in repo.get_metadata("cleared")
    assert repo.get_metadata("cleared")["comment_revision"] == 1
    assert repo.get_metadata("orphan")["comment"] == "Keep orphan user text"
    assert repo.get_metadata("new")["comment"] == "Imported default"
    assert (repo.get_metadata("old-config") is None) == wipe


def test_metadata_migration_dry_run_does_not_write(repo, monkeypatch, tmp_path):
    script = _load_script(monkeypatch, "migrate_metadata")
    monkeypatch.setattr("gamatrix.storage.dynamo.get_repository", lambda: repo)
    config = tmp_path / "config.yaml"
    config.write_text("metadata:\n  Edited:\n    comment: YAML default\n")
    repo.put_metadata({"slug": "edited", "comment": "User text", "comment_revision": 1})
    before = repo.get_all_metadata()
    monkeypatch.setattr(
        sys, "argv", ["migrate_metadata.py", str(config), "--wipe", "--dry-run"]
    )
    script.main()
    assert repo.get_all_metadata() == before


def test_seed_users_main_creates_requested_user(repo, monkeypatch):
    seed_users = _load_script(monkeypatch, "seed_users")
    monkeypatch.setattr(seed_users, "get_repository", lambda: repo)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "seed_users.py",
            "--email",
            "alice@example.com",
            "--username",
            "alice",
            "--password",
            "s3cret",
            "--user-id",
            "42",
            "--admin",
        ],
    )

    seed_users.main()

    user = repo.get_user("alice@example.com")
    assert user is not None
    assert user["email"] == "alice@example.com"
    assert user["username"] == "alice"
    assert user["user_id"] == "42"
    assert user["is_admin"] is True
    assert user["password_hash"] != "s3cret"


def test_seed_users_main_defaults_seeds_from_config(repo, monkeypatch, tmp_path):
    seed_users = _load_script(monkeypatch, "seed_users")
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "seed_users.json").write_text(
        json.dumps(
            [
                {
                    "email": "user1@example.com",
                    "username": "user1",
                    "user_id": "1001",
                    "admin": True,
                },
                {
                    "email": "user2@example.com",
                    "username": "user2",
                    "user_id": "1002",
                    "pic": "avatar.png",
                },
            ]
        )
    )

    monkeypatch.setenv("GAMATRIX_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(seed_users, "get_repository", lambda: repo)
    monkeypatch.setattr(sys, "argv", ["seed_users.py", "--defaults"])

    seed_users.main()

    users = sorted(repo.scan_users(), key=lambda user: user["email"])
    assert [user["email"] for user in users] == [
        "user1@example.com",
        "user2@example.com",
    ]
    assert users[0]["is_admin"] is True
    assert users[1]["pic"] == "avatar.png"


def test_init_local_main_creates_local_stack_state_and_is_idempotent(
    monkeypatch, tmp_path
):
    init_local = _load_script(monkeypatch, "init_local")
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "seed_users.json").write_text(
        json.dumps(
            [
                {
                    "email": "local1@example.com",
                    "username": "local1",
                    "user_id": "1001",
                    "admin": True,
                }
            ]
        )
    )

    settings = _local_settings("localdev")
    monkeypatch.setenv("GAMATRIX_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(init_local, "get_settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["init_local.py"])

    with mock_aws():
        import gamatrix.storage.dynamo as dynamo

        original_repo = dynamo._repo
        dynamo._repo = Repository(settings=settings)
        try:
            init_local.main()
            init_local.main()

            ddb = boto3.client("dynamodb", region_name=settings.aws_region)
            tables = set(ddb.list_tables()["TableNames"])
            assert tables.issuperset(
                {
                    settings.games_table,
                    settings.users_table,
                    settings.libraries_table,
                    settings.jobs_table,
                    settings.metadata_table,
                    settings.profile_pics_table,
                    settings.config_table,
                    settings.passkeys_table,
                    settings.auth_challenges_table,
                }
            )

            s3 = boto3.client("s3", region_name=settings.aws_region)
            buckets = {bucket["Name"] for bucket in s3.list_buckets()["Buckets"]}
            assert settings.upload_bucket in buckets

            repo = dynamo._repo
            assert repo.get_config("hidden") == []
            assert repo.get_config("single_player") == []

            users = repo.scan_users()
            assert [user["email"] for user in users] == ["local1@example.com"]
        finally:
            dynamo._repo = original_repo


def test_init_local_main_can_skip_default_user_seeding(monkeypatch, tmp_path):
    init_local = _load_script(monkeypatch, "init_local")
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "seed_users.json").write_text(
        json.dumps(
            [
                {
                    "email": "local1@example.com",
                    "username": "local1",
                    "user_id": "1001",
                    "admin": True,
                }
            ]
        )
    )

    settings = _local_settings("localdev-skip-users")
    monkeypatch.setenv("GAMATRIX_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(init_local, "get_settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["init_local.py", "--skip-default-users"])

    with mock_aws():
        import gamatrix.storage.dynamo as dynamo

        original_repo = dynamo._repo
        dynamo._repo = Repository(settings=settings)
        try:
            init_local.main()

            repo = dynamo._repo
            assert repo.get_config("hidden") == []
            assert repo.get_config("single_player") == []
            assert repo.scan_users() == []
        finally:
            dynamo._repo = original_repo


def test_local_worker_main_processes_pending_jobs_once(repo, monkeypatch):
    local_worker = _load_script(monkeypatch, "local_worker")
    repo.put_job(
        {
            "job_id": "pending-job",
            "status": JOB_PENDING,
            "created_at": now_iso(),
            "release_keys": ["steam_1"],
            "total": 1,
            "completed_count": 0,
        }
    )
    repo.put_job(
        {
            "job_id": "done-job",
            "status": JOB_COMPLETED,
            "created_at": now_iso(),
            "release_keys": ["steam_2"],
            "total": 1,
            "completed_count": 1,
            "completed_at": now_iso(),
        }
    )

    processed: list[str] = []

    async def fake_run_job(job_id: str, repo_arg) -> None:
        processed.append(job_id)
        repo_arg.update_job(
            job_id,
            {"status": JOB_COMPLETED, "completed_at": now_iso()},
        )

    class StopPolling(Exception):
        """Raised by the patched sleep to stop the infinite worker loop."""

    def stop_after_one_poll(_seconds: int) -> None:
        raise StopPolling

    monkeypatch.setattr(local_worker, "get_repository", lambda: repo)
    monkeypatch.setattr(local_worker, "run_job", fake_run_job)
    monkeypatch.setattr(local_worker.time, "sleep", stop_after_one_poll)

    with pytest.raises(StopPolling):
        local_worker.main()

    assert processed == ["pending-job"]
    assert repo.get_job("pending-job")["status"] == JOB_COMPLETED
    assert repo.get_job("done-job")["status"] == JOB_COMPLETED
