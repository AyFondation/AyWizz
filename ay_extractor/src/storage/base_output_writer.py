# src/storage/base_output_writer.py — v1
"""Abstract output writer interface.

See spec §30.3 for full documentation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseOutputWriter(ABC):
    """Unified interface for output storage backends."""

    @abstractmethod
    async def write(self, path: str, content: bytes | str) -> None:
        """Write content to the given path."""

    @abstractmethod
    async def read(self, path: str) -> bytes:
        """Read content from the given path."""

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """Check if path exists."""

    @abstractmethod
    async def copy(self, src: str, dst: str) -> None:
        """Copy file or directory (used for run carries)."""

    @abstractmethod
    async def create_symlink(self, target: str, link: str) -> None:
        """Create symbolic link (or equivalent for remote storage)."""

    @abstractmethod
    async def list_dir(self, path: str) -> list[str]:
        """List directory contents."""
