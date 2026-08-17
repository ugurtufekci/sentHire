"""S3-compatible object storage (MinIO in dev). Browser uploads go direct-to-S3
via presigned PUT URLs; the API never proxies file bytes (docs/01 §3)."""

import uuid
from functools import lru_cache

import boto3
from botocore.config import Config

from senthire.config import get_settings


@lru_cache
def _s3():
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def upload_key(org_id: uuid.UUID, job_id: uuid.UUID, filename: str) -> str:
    safe = filename.replace("/", "_")[-128:]
    return f"org/{org_id}/jobs/{job_id}/uploads/{uuid.uuid4()}/{safe}"


def presign_put(key: str, content_type: str) -> str:
    settings = get_settings()
    return _s3().generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.s3_bucket, "Key": key, "ContentType": content_type},
        ExpiresIn=settings.presign_expiry_seconds,
    )


def presign_get(key: str) -> str:
    settings = get_settings()
    return _s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=settings.presign_expiry_seconds,
    )


def get_object_bytes(key: str) -> bytes:
    settings = get_settings()
    return _s3().get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()


def object_size(key: str) -> int:
    settings = get_settings()
    return _s3().head_object(Bucket=settings.s3_bucket, Key=key)["ContentLength"]


def ensure_bucket() -> None:
    """Dev convenience (MinIO). Production buckets are provisioned, not created here."""
    settings = get_settings()
    client = _s3()
    existing = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
    if settings.s3_bucket not in existing:
        client.create_bucket(Bucket=settings.s3_bucket)
