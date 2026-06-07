"""DynamoDB access layer.

All table reads/writes go through the Repository class so the rest of the app
never touches boto3 directly. The same code runs against DynamoDB in AWS and
dynamodb-local in development; only the endpoint URL differs.
"""

from __future__ import annotations

import decimal
from typing import TYPE_CHECKING, Any, Iterable, cast

import boto3
from boto3.dynamodb.conditions import Key

from gamatrix.config import Settings, get_settings
from gamatrix.helpers import now_iso

if TYPE_CHECKING:
    # Annotation-only; importing at runtime would cycle (jobs imports Repository).
    from gamatrix.jobs import JobRecord


def _to_dynamo(value: Any) -> Any:
    """Recursively convert floats to Decimal (DynamoDB rejects floats)."""
    if isinstance(value, float):
        # str() round-trips cleanly through Decimal and avoids binary noise.
        return decimal.Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_dynamo(v) for v in value]
    return value


def _from_dynamo(value: Any) -> Any:
    """Recursively convert Decimal back to int/float for app code."""
    if isinstance(value, decimal.Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(v) for v in value]
    return value


class Repository:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._resource = boto3.resource(
            "dynamodb",
            region_name=self.settings.aws_region,
            endpoint_url=self.settings.dynamodb_endpoint_url,
        )

    def _table(self, name: str):
        return self._resource.Table(name)

    # ------------------------------------------------------------------
    # games
    # ------------------------------------------------------------------
    def get_game(self, release_key: str) -> dict | None:
        resp = self._table(self.settings.games_table).get_item(
            Key={"release_key": release_key}
        )
        item = resp.get("Item")
        return _from_dynamo(item) if item else None

    def batch_get_games(self, release_keys: Iterable[str]) -> dict[str, dict]:
        keys = list(dict.fromkeys(release_keys))  # de-dupe, preserve order
        result: dict[str, dict] = {}
        # BatchGetItem caps at 100 keys per request.
        for i in range(0, len(keys), 100):
            chunk = keys[i : i + 100]
            resp = self._resource.batch_get_item(
                RequestItems={
                    self.settings.games_table: {
                        "Keys": [{"release_key": k} for k in chunk]
                    }
                }
            )
            for item in resp["Responses"].get(self.settings.games_table, []):
                game = _from_dynamo(item)
                result[game["release_key"]] = game
        return result

    def put_game(self, game: dict) -> None:
        self._table(self.settings.games_table).put_item(Item=_to_dynamo(game))

    def scan_all_games(self) -> list[dict]:
        return self._scan(self.settings.games_table)

    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------
    def get_user(self, email: str) -> dict | None:
        resp = self._table(self.settings.users_table).get_item(
            Key={"email": email.lower()}
        )
        item = resp.get("Item")
        return _from_dynamo(item) if item else None

    def get_user_by_user_id(self, user_id: str) -> dict | None:
        # Small table; a filtered scan is fine at this scale.
        for user in self.scan_users():
            if str(user.get("user_id")) == str(user_id):
                return user
        return None

    def scan_users(self) -> list[dict]:
        return self._scan(self.settings.users_table)

    def put_user(self, user: dict) -> None:
        user = {**user, "email": user["email"].lower()}
        self._table(self.settings.users_table).put_item(Item=_to_dynamo(user))

    def update_user(self, email: str, attrs: dict) -> None:
        if not attrs:
            return
        names = {f"#{k}": k for k in attrs}
        values = {f":{k}": _to_dynamo(v) for k, v in attrs.items()}
        expr = "SET " + ", ".join(f"#{k} = :{k}" for k in attrs)
        self._table(self.settings.users_table).update_item(
            Key={"email": email.lower()},
            UpdateExpression=expr,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    # ------------------------------------------------------------------
    # user_libraries  (PK user_id, SK release_key)
    # ------------------------------------------------------------------
    def get_user_library(self, user_id: str) -> list[dict]:
        return self._query_all(
            self.settings.libraries_table,
            KeyConditionExpression=Key("user_id").eq(str(user_id)),
        )

    def replace_user_library(self, user_id: str, entries: list[dict]) -> None:
        """Delete the user's existing library rows and write the new set."""
        table = self._table(self.settings.libraries_table)
        existing = self.get_user_library(user_id)
        with table.batch_writer() as batch:
            for row in existing:
                batch.delete_item(
                    Key={
                        "user_id": str(user_id),
                        "release_key": row["release_key"],
                    }
                )
            for entry in entries:
                item = {**entry, "user_id": str(user_id)}
                batch.put_item(Item=_to_dynamo(item))

    def get_owners_of_release(self, release_key: str) -> list[str]:
        """Return user_ids that own a release, via the release_key GSI."""
        items = self._query_all(
            self.settings.libraries_table,
            IndexName="release_key-index",
            KeyConditionExpression=Key("release_key").eq(release_key),
        )
        return [i["user_id"] for i in items]

    # ------------------------------------------------------------------
    # enrichment_jobs
    # ------------------------------------------------------------------
    def put_job(self, job: JobRecord) -> None:
        self._table(self.settings.jobs_table).put_item(Item=_to_dynamo(job))

    def get_job(self, job_id: str) -> JobRecord | None:
        resp = self._table(self.settings.jobs_table).get_item(Key={"job_id": job_id})
        item = resp.get("Item")
        return _from_dynamo(item) if item else None

    def update_job(self, job_id: str, attrs: dict) -> None:
        names = {f"#{k}": k for k in attrs}
        values = {f":{k}": _to_dynamo(v) for k, v in attrs.items()}
        expr = "SET " + ", ".join(f"#{k} = :{k}" for k in attrs)
        self._table(self.settings.jobs_table).update_item(
            Key={"job_id": job_id},
            UpdateExpression=expr,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    def set_job_progress(self, job_id: str, completed_count: int) -> None:
        """Record absolute progress. Idempotent across SQS redeliveries: a
        retried or concurrently-running enricher converges on the same value
        instead of inflating an atomic counter past `total` (see #131). Also
        stamps `updated_at` so staleness is measured from the last progress,
        not job creation."""
        self.update_job(
            job_id, {"completed_count": completed_count, "updated_at": now_iso()}
        )

    def list_pending_jobs(self) -> list[dict]:
        from gamatrix.constants import JOB_PENDING

        return [
            j
            for j in self._scan(self.settings.jobs_table)
            if j.get("status") == JOB_PENDING
        ]

    def fail_stale_jobs(self, jobs: Iterable[JobRecord] | None = None) -> list[str]:
        """Mark presumed-dead jobs (see `is_job_stale`) as failed and return
        their ids.

        Self-heals jobs whose enricher crashed or hit its hard timeout without
        writing a terminal status: left alone they pin the progress bar and
        block new enrichment forever. This is the application-side backstop to
        the queue's redrive-to-DLQ policy, which only caps redeliveries and
        can't update the job record. Pass `jobs` to reuse an existing scan."""
        # Imported here, not at module scope: gamatrix.jobs imports Repository
        # from this module, so a top-level import would be circular.
        from gamatrix.constants import JOB_FAILED
        from gamatrix.jobs import is_job_active, is_job_stale

        if jobs is None:
            jobs = cast("list[JobRecord]", self._scan(self.settings.jobs_table))
        reaped: list[str] = []
        for j in jobs:
            if is_job_active(j) and is_job_stale(j):
                self.update_job(
                    j["job_id"],
                    {"status": JOB_FAILED, "completed_at": now_iso()},
                )
                reaped.append(j["job_id"])
        return reaped

    def get_active_job(self) -> JobRecord | None:
        """Return the most recently created pending-or-running job, if any.

        Stale jobs (presumed-dead enrichers, see `is_job_stale`) are reaped in
        passing so a job that never reached a terminal status can't pin the
        progress bar on every page load, nor block new enrichment from being
        queued."""
        # Imported here, not at module scope: gamatrix.jobs imports Repository
        # from this module, so a top-level import would be circular.
        from gamatrix.jobs import is_job_active

        jobs = cast("list[JobRecord]", self._scan(self.settings.jobs_table))
        reaped = set(self.fail_stale_jobs(jobs))
        active = [j for j in jobs if is_job_active(j) and j["job_id"] not in reaped]
        if not active:
            return None
        return max(active, key=lambda j: j.get("created_at", ""))

    # ------------------------------------------------------------------
    # metadata_overrides  (PK slug)
    # ------------------------------------------------------------------
    def get_all_metadata(self) -> dict[str, dict]:
        return {m["slug"]: m for m in self._scan(self.settings.metadata_table)}

    def put_metadata(self, override: dict) -> None:
        self._table(self.settings.metadata_table).put_item(Item=_to_dynamo(override))

    # ------------------------------------------------------------------
    # config  (PK key -> value)  used locally for hidden/single-player lists
    # ------------------------------------------------------------------
    def get_config(self, key: str, default: Any = None) -> Any:
        resp = self._table(self.settings.config_table).get_item(Key={"key": key})
        item = resp.get("Item")
        return _from_dynamo(item)["value"] if item else default

    def put_config(self, key: str, value: Any) -> None:
        self._table(self.settings.config_table).put_item(
            Item=_to_dynamo({"key": key, "value": value})
        )

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------
    def _scan(self, table_name: str) -> list[dict]:
        table = self._table(table_name)
        items: list[dict] = []
        kwargs: dict[str, Any] = {}
        while True:
            resp = table.scan(**kwargs)
            items.extend(_from_dynamo(i) for i in resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                return items
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    def _query_all(self, table_name: str, **kwargs: Any) -> list[dict]:
        """Run a query, following LastEvaluatedKey so a large result set
        (a single query page caps at 1 MB) is returned in full."""
        table = self._table(table_name)
        items: list[dict] = []
        while True:
            resp = table.query(**kwargs)
            items.extend(_from_dynamo(i) for i in resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                return items
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


_repo: Repository | None = None


def get_repository() -> Repository:
    global _repo
    if _repo is None:
        _repo = Repository()
    return _repo
