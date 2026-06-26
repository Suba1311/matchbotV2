"""Local runtime: local filesystem + Postgres. The default for the CLI."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING

from matchbot.runtime.base import FileSystem, Runtime
from matchbot.storage.postgres import make_repository

if TYPE_CHECKING:
    from matchbot.config.settings import Settings
    from matchbot.storage.base import Repository


def _to_path(uri: str) -> Path:
    return Path(uri[len("file://") :] if uri.startswith("file://") else uri)


class LocalFileSystem(FileSystem):
    """Reads/writes the local disk."""

    def list(self, uri: str, glob: str) -> list[str]:
        base = _to_path(uri)
        if base.is_file():
            return [str(base)]
        return sorted(
            str(p) for p in base.iterdir() if p.is_file() and fnmatch.fnmatch(p.name, glob)
        )

    def read_bytes(self, uri: str) -> bytes:
        return _to_path(uri).read_bytes()

    def write_bytes(self, uri: str, data: bytes) -> None:
        path = _to_path(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


class LocalRuntime(Runtime):
    name = "local"

    def filesystem(self) -> FileSystem:
        return LocalFileSystem()

    def repository(self, settings: Settings) -> Repository:
        return make_repository(settings)
