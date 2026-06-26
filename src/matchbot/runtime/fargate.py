"""Fargate runtime: S3 filesystem + RDS Postgres.

Identical pipeline core to local — only file access changes (S3 instead of disk).
boto3 is imported lazily so the core never depends on it; install with the
``[aws]`` extra. The repository is the same Postgres repository pointed at RDS
via ``DATABASE_URL``.
"""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from matchbot.runtime.base import FileSystem, Runtime
from matchbot.storage.postgres import make_repository

if TYPE_CHECKING:
    from matchbot.config.settings import Settings
    from matchbot.storage.base import Repository


def _split_s3(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    return parsed.netloc, parsed.path.lstrip("/")


class S3FileSystem(FileSystem):
    """Reads/writes S3. Requires the ``[aws]`` extra (boto3)."""

    def __init__(self) -> None:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise ImportError(
                "S3FileSystem requires boto3. Install with: pip install 'matchbot[aws]'"
            ) from exc
        self._s3 = boto3.client("s3")

    def list(self, uri: str, glob: str) -> list[str]:
        bucket, prefix = _split_s3(uri)
        paginator = self._s3.get_paginator("list_objects_v2")
        out: list[str] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if fnmatch.fnmatch(key.rsplit("/", 1)[-1], glob):
                    out.append(f"s3://{bucket}/{key}")
        return sorted(out)

    def read_bytes(self, uri: str) -> bytes:
        bucket, key = _split_s3(uri)
        data: bytes = self._s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        return data

    def write_bytes(self, uri: str, data: bytes) -> None:
        bucket, key = _split_s3(uri)
        self._s3.put_object(Bucket=bucket, Key=key, Body=data)


class FargateRuntime(Runtime):
    name = "fargate"

    def filesystem(self) -> FileSystem:
        return S3FileSystem()

    def repository(self, settings: Settings) -> Repository:
        return make_repository(settings)
