"""Runtime and FileSystem interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from matchbot.config.settings import Settings
    from matchbot.storage.base import Repository


class FileSystem(ABC):
    """Read/list/write bytes by URI. Hides local vs. S3 vs. stage differences."""

    @abstractmethod
    def list(self, uri: str, glob: str) -> list[str]:
        """List object URIs under ``uri`` matching ``glob``."""

    @abstractmethod
    def read_bytes(self, uri: str) -> bytes:
        """Read an object's full contents."""

    @abstractmethod
    def write_bytes(self, uri: str, data: bytes) -> None:
        """Write bytes to ``uri`` (used for optional artifact output)."""


class Runtime(ABC):
    """Assembles platform-specific pieces and parses invocation arguments."""

    name: str

    @abstractmethod
    def filesystem(self) -> FileSystem:
        """Return the filesystem adapter for this runtime."""

    @abstractmethod
    def repository(self, settings: Settings) -> Repository:
        """Return the storage repository for this runtime."""
