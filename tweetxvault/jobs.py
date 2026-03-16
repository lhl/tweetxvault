"""Shared helpers for archive batch jobs."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from tweetxvault.config import AppConfig, XDGPaths, ensure_paths, load_config
from tweetxvault.exceptions import ConfigError
from tweetxvault.storage import ArchiveStore, open_archive_store
from tweetxvault.sync import ProcessLock


@dataclass(slots=True)
class LockedArchiveJob:
    config: AppConfig
    paths: XDGPaths
    store: ArchiveStore
    _dirty: bool = False

    def mark_dirty(self) -> None:
        self._dirty = True


def resolve_job_context(
    *,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
) -> tuple[AppConfig, XDGPaths]:
    if config is None or paths is None:
        loaded_config, loaded_paths = load_config()
        config = config or loaded_config
        paths = paths or loaded_paths
    assert config is not None
    assert paths is not None
    return config, ensure_paths(paths)


@asynccontextmanager
async def locked_archive_job(
    *,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
) -> AsyncIterator[LockedArchiveJob]:
    config, paths = resolve_job_context(config=config, paths=paths)
    lock = ProcessLock(paths.lock_file)
    lock.acquire()
    try:
        store = open_archive_store(paths, create=False)
        if store is None:
            raise ConfigError("No local archive found.")
        job = LockedArchiveJob(config=config, paths=paths, store=store)
        try:
            yield job
            if job._dirty:
                store.optimize()
        finally:
            store.close()
    finally:
        lock.release()
