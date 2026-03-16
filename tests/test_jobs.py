from __future__ import annotations

import pytest

import tweetxvault.jobs as jobs
from tweetxvault.exceptions import ConfigError


class _FakeStore:
    def __init__(self) -> None:
        self.optimize_calls = 0
        self.closed = False

    def optimize(self) -> None:
        self.optimize_calls += 1

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
