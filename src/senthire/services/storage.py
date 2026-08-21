"""Object storage. Browser uploads go direct-to-S3 via presigned PUT URLs; the
API never proxies file bytes in production (docs/01 §3).

A `local` backend exists for development and offline demos: it keeps objects on
disk and hands out API URLs instead of presigned ones, so the whole product runs
without MinIO, AWS, or any network at all. Everything above this module is
unaware of which backend is in use.
"""

import hashlib
import re
import uuid
from functools import lru_cache
from pathlib import Path

import boto3
from botocore.config import Config

from senthire.config import get_settings

# org/<uuid>/jobs/<uuid>/uploads/<uuid>/<filename> — anything else is not a key
# this application minted, and the local backend refuses to touch it.
KEY_PATTERN = re.compile(
    r"^org/[0-9a-f-]{36}/jobs/[0-9a-f-]{36}/uploads/[0-9a-f-]{36}/[^/]{1,128}$"
)


def _local_root() -> Path:
    return Path(get_settings().local_storage_dir)


def local_path(key: str) -> Path:
    """Filesystem location of a key, refusing anything that isn't one of ours."""
    if not KEY_PATTERN.match(key):
        raise ValueError(f"refusing suspicious storage key: {key[:120]}")
    # Hashed leaf: the key is already validated, and hashing removes any doubt
    # about what reaches the filesystem.
    digest = hashlib.sha256(key.encode()).hexdigest()
    return _local_root() / digest[:2] / digest


def _is_local() -> bool:
    return get_settings().storage_backend == "local"


def _client(endpoint_url: str | None):
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


@lru_cache
def _s3():
    return _client(get_settings().s3_endpoint_url)


@lru_cache
def _s3_public():
    """Presigning client bound to the browser-reachable endpoint (the SigV4
    signature includes the host, so presigned URLs must be minted against it)."""
    settings = get_settings()
    return _client(settings.s3_public_endpoint_url or settings.s3_endpoint_url)


def upload_key(org_id: uuid.UUID, job_id: uuid.UUID, filename: str) -> str:
    safe = filename.replace("/", "_")[-128:]
    return f"org/{org_id}/jobs/{job_id}/uploads/{uuid.uuid4()}/{safe}"


def presign_put(key: str, content_type: str) -> str:
    settings = get_settings()
    if _is_local():
        return f"{settings.app_base_url}/api/v1/storage/{key}"
    return _s3_public().generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.s3_bucket, "Key": key, "ContentType": content_type},
        ExpiresIn=settings.presign_expiry_seconds,
    )


def presign_get(key: str) -> str:
    settings = get_settings()
    if _is_local():
        return f"{settings.app_base_url}/api/v1/storage/{key}"
    return _s3_public().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=settings.presign_expiry_seconds,
    )


def put_object_bytes(key: str, data: bytes) -> None:
    """Local backend only — the API receives the bytes instead of S3."""
    path = local_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def get_object_bytes(key: str) -> bytes:
    if _is_local():
        return local_path(key).read_bytes()
    settings = get_settings()
    return _s3().get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()


def delete_object(key: str) -> None:
    """Remove one object. Missing objects are fine — erasure is idempotent."""
    if _is_local():
        try:
            local_path(key).unlink(missing_ok=True)
        except ValueError:
            pass  # not a key we minted; nothing of ours to delete
        return
    settings = get_settings()
    _s3().delete_object(Bucket=settings.s3_bucket, Key=key)


def object_size(key: str) -> int:
    if _is_local():
        return local_path(key).stat().st_size
    settings = get_settings()
    return _s3().head_object(Bucket=settings.s3_bucket, Key=key)["ContentLength"]


def ensure_bucket() -> None:
    """Dev convenience (MinIO). Production buckets are provisioned, not created here."""
    if _is_local():
        _local_root().mkdir(parents=True, exist_ok=True)
        return
    settings = get_settings()
    client = _s3()
    existing = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
    if settings.s3_bucket not in existing:
        client.create_bucket(Bucket=settings.s3_bucket)
