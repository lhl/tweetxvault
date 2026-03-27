from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

import tweetxvault.jobs as jobs
from tweetxvault.exceptions import ConfigError


class _FakeStore:
    def __init__(
        self,
        *,
        version_counts: list[int] | None = None,
        optimize_exc: BaseException | None = None,
    ) -> None:
        self.optimize_calls = 0
        self.closed = False
        self._version_counts = version_counts or [0]
        self._version_index = 0
        self._optimize_exc = optimize_exc

    def version_count(self) -> int:
        value = self._version_counts[min(self._version_index, len(self._version_counts) - 1)]
        self._version_index += 1
        return value

    def optimize(self) -> None:
        self.optimize_calls += 1
        if self._optimize_exc is not None:
            raise self._optimize_exc

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_locked_archive_job_optimizes_when_marked_dirty(
    paths,
    config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore()
    monkeypatch.setattr(jobs, "open_archive_store", lambda _paths, create=False: store)

    async with jobs.locked_archive_job(config=config, paths=paths) as job:
        assert job.config == config
        assert job.paths == paths
        assert job.store is store
        job.mark_dirty()

    assert store.optimize_calls == 1
    assert store.closed is True


@pytest.mark.asyncio
async def test_locked_archive_job_skips_optimize_without_changes(
    paths,
    config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore()
    monkeypatch.setattr(jobs, "open_archive_store", lambda _paths, create=False: store)

    async with jobs.locked_archive_job(config=config, paths=paths) as job:
        assert job.store is store

    assert store.optimize_calls == 0
    assert store.closed is True


@pytest.mark.asyncio
async def test_locked_archive_job_interrupt_optimizes_after_substantial_writes(
    paths,
    config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, color_system=None)
    store = _FakeStore()
    monkeypatch.setattr(jobs, "open_archive_store", lambda _paths, create=False: store)

    with pytest.raises(KeyboardInterrupt):
        async with jobs.locked_archive_job(config=config, paths=paths, console=console) as job:
            job.mark_dirty(rows=100, batches=1)
            raise KeyboardInterrupt()

    assert store.optimize_calls == 1
    assert store.closed is True
    assert "interrupt received, compacting archive before exit" in buffer.getvalue()


@pytest.mark.asyncio
async def test_locked_archive_job_interrupt_skips_optimize_for_small_writes(
    paths,
    config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore(version_counts=[0, 1])
    monkeypatch.setattr(jobs, "open_archive_store", lambda _paths, create=False: store)

    with pytest.raises(KeyboardInterrupt):
        async with jobs.locked_archive_job(config=config, paths=paths) as job:
            job.mark_dirty()
            raise KeyboardInterrupt()

    assert store.optimize_calls == 0
    assert store.closed is True


@pytest.mark.asyncio
async def test_locked_archive_job_interrupt_warns_when_optimize_is_interrupted(
    paths,
    config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, color_system=None)
    store = _FakeStore(optimize_exc=KeyboardInterrupt())
    monkeypatch.setattr(jobs, "open_archive_store", lambda _paths, create=False: store)

    with pytest.raises(KeyboardInterrupt):
        async with jobs.locked_archive_job(config=config, paths=paths, console=console) as job:
            job.mark_dirty(rows=100, batches=1)
            raise KeyboardInterrupt()

    assert store.optimize_calls == 1
    assert store.closed is True
    assert "optimize interrupted; run 'tweetxvault optimize' later" in buffer.getvalue()


@pytest.mark.asyncio
async def test_locked_archive_job_loads_context_and_errors_when_archive_missing(
    paths,
    config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs, "load_config", lambda: (config, paths))
    monkeypatch.setattr(jobs, "open_archive_store", lambda _paths, create=False: None)

    with pytest.raises(ConfigError, match="No local archive found."):
        async with jobs.locked_archive_job():
            raise AssertionError("context should not yield without an archive")
