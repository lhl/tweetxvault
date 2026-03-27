"""Shared helpers for archive batch jobs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from rich.console import Console

from tweetxvault.config import AppConfig, XDGPaths, ensure_paths, load_config
from tweetxvault.exceptions import ConfigError
from tweetxvault.storage import ArchiveStore, open_archive_store

_INTERRUPT_OPTIMIZE_MIN_BATCHES = 4
_INTERRUPT_OPTIMIZE_MIN_ROWS = 100
_INTERRUPT_OPTIMIZE_MIN_VERSION_DELTA = 4
_INTERRUPT_OPTIMIZE_MESSAGE = (
    "interrupt received, compacting archive before exit; press Ctrl-C again to skip"
)
_INTERRUPT_OPTIMIZE_ABORTED_MESSAGE = "optimize interrupted; run 'tweetxvault optimize' later"


def _safe_version_count(store: ArchiveStore) -> int | None:
    try:
        return store.version_count()
    except Exception:
        return None


def is_interrupt_exception(exc: BaseException) -> bool:
    return isinstance(exc, KeyboardInterrupt | asyncio.CancelledError)


@dataclass(slots=True)
class ArchiveWriteTracker:
    store: ArchiveStore
    batch_writes: int = 0
    row_writes: int = 0
    start_version_count: int | None = None

    def __post_init__(self) -> None:
        if self.start_version_count is None:
            self.start_version_count = _safe_version_count(self.store)

    def mark_dirty(self, rows: int = 1, batches: int = 1) -> None:
        if rows > 0:
            self.row_writes += rows
        if batches > 0:
            self.batch_writes += batches

    @property
    def version_delta(self) -> int | None:
        current = _safe_version_count(self.store)
        if current is None or self.start_version_count is None:
            return None
        return max(current - self.start_version_count, 0)

    @property
    def has_writes(self) -> bool:
        delta = self.version_delta
        return self.batch_writes > 0 or self.row_writes > 0 or (delta is not None and delta > 0)

    def should_optimize_on_interrupt(self) -> bool:
        delta = self.version_delta
        return (
            self.batch_writes >= _INTERRUPT_OPTIMIZE_MIN_BATCHES
            or self.row_writes >= _INTERRUPT_OPTIMIZE_MIN_ROWS
            or (delta is not None and delta >= _INTERRUPT_OPTIMIZE_MIN_VERSION_DELTA)
        )


def best_effort_interrupt_optimize(
    store: ArchiveStore,
    tracker: ArchiveWriteTracker,
    *,
    console: Console | None = None,
) -> bool:
    if not tracker.should_optimize_on_interrupt():
        return False
    if console is not None:
        console.print(_INTERRUPT_OPTIMIZE_MESSAGE, highlight=False)
    try:
        store.optimize()
    except KeyboardInterrupt:
        if console is not None:
            console.print(f"[yellow]{_INTERRUPT_OPTIMIZE_ABORTED_MESSAGE}[/yellow]")
        return False
    except Exception as exc:
        if console is not None:
            console.print(
                "[yellow]archive optimize failed during interrupt cleanup; "
                f"run 'tweetxvault optimize' later ({exc})[/yellow]",
                highlight=False,
            )
        return False
    return True


@dataclass(slots=True)
class LockedArchiveJob:
    config: AppConfig
    paths: XDGPaths
    store: ArchiveStore
    write_tracker: ArchiveWriteTracker

    def mark_dirty(self, rows: int = 1, batches: int = 1) -> None:
        self.write_tracker.mark_dirty(rows=rows, batches=batches)


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
    console: Console | None = None,
) -> AsyncIterator[LockedArchiveJob]:
    from tweetxvault.sync import ProcessLock

    config, paths = resolve_job_context(config=config, paths=paths)
    lock = ProcessLock(paths.lock_file)
    lock.acquire()
    try:
        store = open_archive_store(paths, create=False)
        if store is None:
            raise ConfigError("No local archive found.")
        tracker = ArchiveWriteTracker(store)
        job = LockedArchiveJob(
            config=config,
            paths=paths,
            store=store,
            write_tracker=tracker,
        )
        try:
            yield job
        except BaseException as exc:
            if is_interrupt_exception(exc):
                best_effort_interrupt_optimize(store, tracker, console=console)
            raise
        else:
            if tracker.has_writes:
                store.optimize()
        finally:
            store.close()
    finally:
        lock.release()
