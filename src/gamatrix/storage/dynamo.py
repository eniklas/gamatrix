"""DynamoDB access layer.

All table reads/writes go through the Repository class so the rest of the app
never touches boto3 directly. The same code runs against DynamoDB in AWS and
dynamodb-local in development; only the endpoint URL differs.
"""

from __future__ import annotations

import decimal
from typing import Any, Iterable

import boto3
from boto3.dynamodb.conditions import Key

from gamatrix.config import Settings, get_settings


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
        resp = self._table(self.settings.libraries_table).query(
            KeyConditionExpression=Key("user_id").eq(str(user_id))
        )
        return [_from_dynamo(i) for i in resp.get("Items", [])]

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
        resp = self._table(self.settings.libraries_table).query(
            IndexName="release_key-index",
            KeyConditionExpression=Key("release_key").eq(release_key),
        )
        return [_from_dynamo(i)["user_id"] for i in resp.get("Items", [])]

    # ------------------------------------------------------------------
    # enrichment_jobs
    # ------------------------------------------------------------------
    def put_job(self, job: dict) -> None:
        self._table(self.settings.jobs_table).put_item(Item=_to_dynamo(job))

    def get_job(self, job_id: str) -> dict | None:
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

    def increment_job_progress(self, job_id: str, by: int = 1) -> None:
        self._table(self.settings.jobs_table).update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET completed_count = completed_count + :n",
            ExpressionAttributeValues={":n": _to_dynamo(by)},
        )

    def list_pending_jobs(self) -> list[dict]:
        from gamatrix.constants import JOB_PENDING

        return [
            j
            for j in self._scan(self.settings.jobs_table)
            if j.get("status") == JOB_PENDING
        ]

    def get_active_job(self) -> dict | None:
        """Return the most recently created pending-or-running job, if any."""
        from gamatrix.constants import JOB_PENDING, JOB_RUNNING

        active = [
            j
            for j in self._scan(self.settings.jobs_table)
            if j.get("status") in (JOB_PENDING, JOB_RUNNING)
        ]
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


_repo: Repository | None = None


def get_repository() -> Repository:
    global _repo
    if _repo is None:
        _repo = Repository()
    return _repo
