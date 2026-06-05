"""S3 helpers for GOG Galaxy DB uploads.

Uploads bypass the web Lambda (which has request-size limits) by going directly
to S3 via a presigned POST. In AWS the upload triggers the DB-parser Lambda via
an S3 event; locally there is no event, so the upload-complete endpoint invokes
the parser inline.
"""

from __future__ import annotations

import boto3

from gamatrix.config import Settings, get_settings


class S3Storage:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._client = boto3.client(
            "s3",
            region_name=self.settings.aws_region,
            endpoint_url=self.settings.s3_endpoint_url,
        )

    def presigned_upload(self, key: str, max_bytes: int) -> dict:
        """Return {url, fields} for a browser to POST the DB file directly."""
        return self._client.generate_presigned_post(
            Bucket=self.settings.upload_bucket,
            Key=key,
            Conditions=[["content-length-range", 1, max_bytes]],
            ExpiresIn=3600,
        )

    def download(self, key: str, dest_path: str) -> None:
        self._client.download_file(self.settings.upload_bucket, key, dest_path)

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.settings.upload_bucket, Key=key)


_s3: S3Storage | None = None


def get_s3() -> S3Storage:
    global _s3
    if _s3 is None:
        _s3 = S3Storage()
    return _s3
