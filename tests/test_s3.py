"""Tests for S3 presigned upload URL generation."""

from __future__ import annotations

from gamatrix.storage.s3 import S3Storage


def test_presigned_upload_uses_public_endpoint(monkeypatch):
    storage = S3Storage.__new__(S3Storage)
    storage.settings = type(
        "SettingsStub",
        (),
        {"upload_bucket": "bucket", "public_s3_endpoint_url": "http://localhost:9000"},
    )()

    class ClientStub:
        def generate_presigned_post(self, **kwargs):
            return {
                "url": "http://minio:9000/bucket",
                "fields": {"key": kwargs["Key"]},
            }

    storage._client = ClientStub()

    post = storage.presigned_upload("uploads/test.db", 10)

    assert post["url"] == "http://localhost:9000/bucket"
    assert post["fields"]["key"] == "uploads/test.db"


def test_presigned_upload_keeps_signed_url_without_public_override():
    storage = S3Storage.__new__(S3Storage)
    storage.settings = type(
        "SettingsStub",
        (),
        {"upload_bucket": "bucket", "public_s3_endpoint_url": None},
    )()

    class ClientStub:
        def generate_presigned_post(self, **kwargs):
            return {
                "url": "http://minio:9000/bucket",
                "fields": {"key": kwargs["Key"]},
            }

    storage._client = ClientStub()

    post = storage.presigned_upload("uploads/test.db", 10)

    assert post["url"] == "http://minio:9000/bucket"
