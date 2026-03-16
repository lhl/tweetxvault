from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from rich.console import Console

import tweetxvault.cli as cli
from tweetxvault.auth import BrowserCandidate
from tweetxvault.client.timelines import TimelineTweet
from tweetxvault.config import AppConfig, AuthConfig
from tweetxvault.exceptions import ProcessLockError
from tweetxvault.storage import open_archive_store


def _tweet(tweet_id: str, *, text: str) -> TimelineTweet:
    return TimelineTweet(
        tweet_id=tweet_id,
        text=text,
        author_id="1",
        author_username="user1",
        author_display_name="User 1",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="10",
        raw_json={"tweet": tweet_id},
    )


def _seed_archive(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[_tweet("1", text="bookmark tweet")],
        last_head_tweet_id="1",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.persist_page(
        operation="Likes",
        collection_type="like",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[_tweet("2", text="like tweet")],
        last_head_tweet_id="2",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.persist_page(
        operation="UserTweets",
        collection_type="tweet",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[_tweet("3", text="authored tweet")],
        last_head_tweet_id="3",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()


def _capture_console(monkeypatch, buffer: StringIO) -> None:
    monkeypatch.setattr(
        cli,
        "_configure_logging",
        lambda: Console(file=buffer, force_terminal=False, color_system=None),
    )


def test_view_bookmarks_prints_rows(paths, monkeypatch) -> None:
    _seed_archive(paths)
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))

    cli.view_bookmarks(limit=5)

    output = buffer.getvalue()
    assert "bookmark tweet" in output
    assert "bookmarks archive" in output
    assert "like tweet" not in output


def test_view_tweets_prints_rows(paths, monkeypatch) -> None:
    _seed_archive(paths)
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))

    cli.view_tweets(limit=5)

    output = buffer.getvalue()
    assert "authored tweet" in output
    assert "tweets archive" in output
    assert "bookmark tweet" not in output


def test_highlight_search_matches_marks_query_terms() -> None:
    rendered = cli._highlight_search_matches(
        "Machine learning beats keyword search for search-heavy tasks.",
        "search learning",
    )

    assert rendered.plain == "Machine learning beats keyword search for search-heavy tasks."
    spans = {(span.start, span.end, span.style) for span in rendered.spans}
    assert (8, 16, "reverse") in spans
    assert (31, 37, "reverse") in spans
    assert (42, 48, "reverse") in spans


def test_export_json_accepts_plural_collection_name(paths, monkeypatch, tmp_path: Path) -> None:
    _seed_archive(paths)
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    out_path = tmp_path / "bookmarks.json"

    cli.export_json(collection="bookmarks", out=out_path)

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert [row["tweet_id"] for row in payload] == ["1"]
    assert "exported bookmarks archive" in buffer.getvalue()


def test_export_json_accepts_tweets_collection_name(paths, monkeypatch, tmp_path: Path) -> None:
    _seed_archive(paths)
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    out_path = tmp_path / "tweets.json"

    cli.export_json(collection="tweets", out=out_path)

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert [row["tweet_id"] for row in payload] == ["3"]
    assert "exported tweets archive" in buffer.getvalue()


def test_export_html_creates_viewer(paths, monkeypatch, tmp_path: Path) -> None:
    _seed_archive(paths)
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    out_path = tmp_path / "bookmarks.html"

    cli.export_html(collection="bookmarks", out=out_path)

    html = out_path.read_text(encoding="utf-8")
    assert "tweetxvault export: bookmarks" in html
    assert "bookmark tweet" in html
    assert "like tweet" not in html
    assert "open on X" in html
    assert "exported bookmarks archive" in buffer.getvalue()


def test_auth_check_interactive_uses_selected_browser(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda: (AppConfig(auth=AuthConfig(auth_token="config-token", ct0="config-ct0")), paths),
    )
    monkeypatch.setattr(
        cli,
        "_pick_browser_candidate_interactively",
        lambda console, browser=None: BrowserCandidate(
            browser_id="chrome",
            browser_name="Chrome",
            profile_name="Default",
            profile_path=Path("/profiles/chrome/Default"),
            is_default=True,
        ),
    )
    selected = {}
    monkeypatch.setattr(
        cli,
        "resolve_auth_bundle",
        lambda config, env=None, status=None: SimpleNamespace(
            auth_token="chrome-token",
            ct0="chrome-ct0",
            user_id="42",
            auth_token_source="chrome",
            ct0_source="chrome",
            user_id_source="chrome",
        ),
    )

    async def fake_run_preflight(*, config, paths, collections, auth_bundle=None):
        selected["auth_bundle"] = auth_bundle
        return SimpleNamespace(
            auth=auth_bundle,
            probes={
                "bookmarks": SimpleNamespace(ready=True, detail="Remote probe succeeded."),
                "likes": SimpleNamespace(ready=True, detail="Remote probe succeeded."),
                "tweets": SimpleNamespace(ready=True, detail="Remote probe succeeded."),
            },
            has_local_error=False,
            has_remote_error=False,
        )

    monkeypatch.setattr(cli, "run_preflight", fake_run_preflight)

    cli.auth_check(interactive=True)

    assert selected["auth_bundle"].auth_token == "chrome-token"
    output = buffer.getvalue()
    assert "local auth: auth_token=chrome" in output
    assert "bookmarks: ready" in output
    assert "tweets: ready" in output


def test_auth_check_debug_auth_prints_resolver_status(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "_prepare_auth_override",
        lambda config, console, **kwargs: (config, None),
    )
    monkeypatch.setattr(
        cli,
        "resolve_auth_bundle",
        lambda config, env=None, status=None: (
            status("trying Firefox browser cookies") if status is not None else None,
            SimpleNamespace(
                auth_token="token",
                ct0="ct0",
                user_id="42",
                auth_token_source="firefox",
                ct0_source="firefox",
                user_id_source="firefox",
            ),
        )[1],
    )

    async def fake_run_preflight(*, config, paths, collections, auth_bundle=None):
        return SimpleNamespace(
            auth=auth_bundle,
            probes={
                "bookmarks": SimpleNamespace(ready=True, detail="Remote probe succeeded."),
                "likes": SimpleNamespace(ready=True, detail="Remote probe succeeded."),
                "tweets": SimpleNamespace(ready=True, detail="Remote probe succeeded."),
            },
            has_local_error=False,
            has_remote_error=False,
        )

    monkeypatch.setattr(cli, "run_preflight", fake_run_preflight)

    cli.auth_check(debug_auth=True)

    output = buffer.getvalue()
    assert "auth: trying Firefox browser cookies" in output
    assert "bookmarks: ready" in output


def test_prepare_auth_override_preserves_explicit_user_id_fallback(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    console = Console(file=StringIO(), force_terminal=False, color_system=None)
    config = AppConfig(
        auth=AuthConfig(
            auth_token="config-token",
            ct0="config-ct0",
            user_id="84",
        )
    )
    captured: dict[str, object] = {}
    monkeypatch.setenv("TWEETXVAULT_USER_ID", "42")

    def fake_resolve_auth_bundle(config, env=None, status=None):
        captured["config_user_id"] = config.auth.user_id
        captured["config_auth_token"] = config.auth.auth_token
        captured["config_ct0"] = config.auth.ct0
        assert env is not None
        captured["env_user_id"] = env.get("TWEETXVAULT_USER_ID")
        captured["env_auth_token"] = env.get("TWEETXVAULT_AUTH_TOKEN")
        captured["env_ct0"] = env.get("TWEETXVAULT_CT0")
        return SimpleNamespace(auth_token="browser-token", ct0="browser-ct0", user_id="42")

    monkeypatch.setattr(cli, "resolve_auth_bundle", fake_resolve_auth_bundle)

    cli._prepare_auth_override(
        config,
        console,
        browser="firefox",
        profile=None,
        profile_path=None,
    )

    assert captured == {
        "config_user_id": "84",
        "config_auth_token": None,
        "config_ct0": None,
        "env_user_id": "42",
        "env_auth_token": None,
        "env_ct0": None,
    }


def test_sync_bookmarks_forwards_article_backfill(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "_prepare_auth_override",
        lambda config, console, **kwargs: (config, SimpleNamespace(auth_token="t")),
    )
    forwarded = {}

    async def fake_sync_collection(
        collection,
        *,
        full,
        backfill=False,
        article_backfill=False,
        limit=None,
        config=None,
        auth_bundle=None,
        console=None,
    ):
        forwarded.update(
            {
                "collection": collection,
                "full": full,
                "backfill": backfill,
                "article_backfill": article_backfill,
                "limit": limit,
            }
        )
        return SimpleNamespace(pages_fetched=2, tweets_seen=3, stop_reason="empty")

    monkeypatch.setattr(cli, "sync_collection", fake_sync_collection)

    cli.sync_bookmarks(article_backfill=True)

    assert forwarded == {
        "collection": "bookmarks",
        "full": False,
        "backfill": False,
        "article_backfill": True,
        "limit": None,
    }
    assert "bookmarks: 2 pages, 3 tweets, empty" in buffer.getvalue()


def test_sync_likes_forwards_article_backfill(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "_prepare_auth_override",
        lambda config, console, **kwargs: (config, SimpleNamespace(auth_token="t")),
    )
    forwarded = {}

    async def fake_sync_collection(
        collection,
        *,
        full,
        backfill=False,
        article_backfill=False,
        limit=None,
        config=None,
        auth_bundle=None,
        console=None,
    ):
        forwarded.update(
            {
                "collection": collection,
                "full": full,
                "backfill": backfill,
                "article_backfill": article_backfill,
                "limit": limit,
            }
        )
        return SimpleNamespace(pages_fetched=2, tweets_seen=3, stop_reason="empty")

    monkeypatch.setattr(cli, "sync_collection", fake_sync_collection)

    cli.sync_likes(article_backfill=True)

    assert forwarded == {
        "collection": "likes",
        "full": False,
        "backfill": False,
        "article_backfill": True,
        "limit": None,
    }
    assert "likes: 2 pages, 3 tweets, empty" in buffer.getvalue()


def test_sync_tweets_forwards_article_backfill(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "_prepare_auth_override",
        lambda config, console, **kwargs: (config, SimpleNamespace(auth_token="t")),
    )
    forwarded = {}

    async def fake_sync_collection(
        collection,
        *,
        full,
        backfill=False,
        article_backfill=False,
        limit=None,
        config=None,
        auth_bundle=None,
        console=None,
    ):
        forwarded.update(
            {
                "collection": collection,
                "full": full,
                "backfill": backfill,
                "article_backfill": article_backfill,
                "limit": limit,
            }
        )
        return SimpleNamespace(pages_fetched=2, tweets_seen=3, stop_reason="empty")

    monkeypatch.setattr(cli, "sync_collection", fake_sync_collection)

    cli.sync_tweets(article_backfill=True)

    assert forwarded == {
        "collection": "tweets",
        "full": False,
        "backfill": False,
        "article_backfill": True,
        "limit": None,
    }
    assert "tweets: 2 pages, 3 tweets, empty" in buffer.getvalue()


def test_sync_all_forwards_article_backfill(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "_prepare_auth_override",
        lambda config, console, **kwargs: (config, SimpleNamespace(auth_token="t")),
    )
    forwarded = {}

    async def fake_sync_all(
        *,
        full,
        backfill=False,
        article_backfill=False,
        limit=None,
        config=None,
        auth_bundle=None,
        console=None,
    ):
        forwarded.update(
            {
                "full": full,
                "backfill": backfill,
                "article_backfill": article_backfill,
                "limit": limit,
            }
        )
        return SimpleNamespace(
            results=[SimpleNamespace(collection="bookmarks", pages_fetched=2, tweets_seen=3)],
            errors={"likes": "boom"},
            exit_code=2,
        )

    monkeypatch.setattr(cli, "sync_all", fake_sync_all)

    with pytest.raises(typer.Exit) as excinfo:
        cli.sync_everything(article_backfill=True)

    assert excinfo.value.exit_code == 2
    assert forwarded == {
        "full": False,
        "backfill": False,
        "article_backfill": True,
        "limit": None,
    }
    output = buffer.getvalue()
    assert "bookmarks: 2 pages, 3 tweets" in output
    assert "likes: failed (boom)" in output


def test_media_download_reports_runner_result(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))

    async def fake_download_media(**kwargs):
        return SimpleNamespace(processed=3, downloaded=2, skipped=1, failed=0)

    monkeypatch.setattr(cli, "download_media", fake_download_media)

    cli.media_download(limit=5, photos_only=True, retry_failed=True)

    output = buffer.getvalue()
    assert "media: 3 processed, 2 downloaded, 1 skipped, 0 failed" in output


def test_unfurl_archive_reports_runner_result(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))

    async def fake_unfurl_urls(**kwargs):
        return SimpleNamespace(processed=2, updated=2, failed=0)

    monkeypatch.setattr(cli, "unfurl_urls", fake_unfurl_urls)

    cli.unfurl_archive(limit=10, retry_failed=True)

    output = buffer.getvalue()
    assert "unfurl: 2 processed, 2 updated, 0 failed" in output


def test_refresh_archived_articles_reports_runner_result(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "_prepare_auth_override",
        lambda config, console, **kwargs: (config, SimpleNamespace(auth_token="token", ct0="ct0")),
    )

    async def fake_refresh_articles(**kwargs):
        assert kwargs["targets"] == ["https://x.com/example/status/2026531440414925307"]
        assert kwargs["preview_only"] is True
        return SimpleNamespace(processed=1, updated=1, failed=0)

    monkeypatch.setattr(cli, "refresh_articles", fake_refresh_articles)

    cli.refresh_archived_articles(["https://x.com/example/status/2026531440414925307"])

    output = buffer.getvalue()
    assert "articles: 1 processed, 1 refreshed, 0 failed" in output


def test_expand_archive_threads_reports_runner_result(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "_prepare_auth_override",
        lambda config, console, **kwargs: (config, SimpleNamespace(auth_token="token", ct0="ct0")),
    )

    async def fake_expand_threads(**kwargs):
        assert kwargs["targets"] == ["https://x.com/example/status/2026531440414925307"]
        assert kwargs["limit"] == 5
        assert kwargs["refresh"] is False
        return SimpleNamespace(processed=2, expanded=2, skipped=1, failed=0)

    monkeypatch.setattr(cli, "expand_threads", fake_expand_threads)

    cli.expand_archive_threads(
        ["https://x.com/example/status/2026531440414925307"],
        limit=5,
    )

    output = buffer.getvalue()
    assert "threads: 2 processed, 2 expanded, 1 skipped, 0 failed" in output


def test_expand_archive_threads_forwards_refresh_flag(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "_prepare_auth_override",
        lambda config, console, **kwargs: (config, SimpleNamespace(auth_token="token", ct0="ct0")),
    )

    async def fake_expand_threads(**kwargs):
        assert kwargs["targets"] == ["100"]
        assert kwargs["refresh"] is True
        return SimpleNamespace(processed=1, expanded=1, skipped=0, failed=0)

    monkeypatch.setattr(cli, "expand_threads", fake_expand_threads)

    cli.expand_archive_threads(["100"], refresh=True)

    assert "threads: 1 processed, 1 expanded, 0 skipped, 0 failed" in buffer.getvalue()


def test_expand_archive_threads_debug_auth_passes_status_callback(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "_prepare_auth_override",
        lambda config, console, **kwargs: (config, None),
    )

    async def fake_expand_threads(**kwargs):
        kwargs["auth_status"]("trying Firefox browser cookies")
        return SimpleNamespace(processed=0, expanded=0, skipped=0, failed=0)

    monkeypatch.setattr(cli, "expand_threads", fake_expand_threads)

    cli.expand_archive_threads(debug_auth=True)

    output = buffer.getvalue()
    assert "auth: trying Firefox browser cookies" in output
    assert "threads: 0 processed, 0 expanded, 0 skipped, 0 failed" in output


def test_expand_archive_threads_refresh_requires_targets(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    monkeypatch.setattr(
        cli,
        "_prepare_auth_override",
        lambda config, console, **kwargs: (config, None),
    )

    with pytest.raises(typer.Exit) as excinfo:
        cli.expand_archive_threads(refresh=True)

    assert excinfo.value.exit_code == 1
    assert "--refresh requires one or more explicit thread targets." in buffer.getvalue()


def test_with_auto_optimize_exits_when_lock_is_held(paths, monkeypatch) -> None:
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, color_system=None)
    attempts = {"count": 0}

    class FakeStore:
        def optimize(self) -> None:
            raise AssertionError("optimize should not run when the lock is unavailable")

    def fail(_store) -> None:
        attempts["count"] += 1
        raise OSError("Too many open files")

    def blocked(_paths, _fn):
        raise ProcessLockError("Another tweetxvault archive job is already running.")

    monkeypatch.setattr(cli, "_with_archive_write_lock", blocked)

    with pytest.raises(typer.Exit) as excinfo:
        cli._with_auto_optimize(FakeStore(), paths, console, fail)

    assert excinfo.value.exit_code == 2
    assert attempts["count"] == 1
    output = buffer.getvalue()
    assert "Another tweetxvault archive job is already running." in output
    assert "Archive optimize is blocked while another job is writing." in output


def test_optimize_archive_uses_write_lock(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    lock_calls: list[Path] = []

    class FakeStore:
        def __init__(self) -> None:
            self.optimized = False
            self.closed = False

        def version_count(self) -> int:
            return 1 if self.optimized else 3

        def optimize(self) -> None:
            self.optimized = True

        def close(self) -> None:
            self.closed = True

    store = FakeStore()
    monkeypatch.setattr(cli, "open_archive_store", lambda _paths, create=False: store)
    monkeypatch.setattr(
        cli,
        "_with_archive_write_lock",
        lambda lock_paths, fn: (lock_calls.append(lock_paths.lock_file), fn())[1],
    )

    cli.optimize_archive()

    assert lock_calls == [paths.lock_file]
    assert store.optimized is True
    assert store.closed is True
    assert "optimized archive: 3 versions -> 1 versions" in buffer.getvalue()


def test_rehydrate_archive_uses_write_lock(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    lock_calls: list[Path] = []

    class FakeTqdm:
        def __init__(self, *args, **kwargs) -> None:
            self.updates: list[int] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def update(self, count: int) -> None:
            self.updates.append(count)

    class FakeStore:
        def __init__(self) -> None:
            self.table = SimpleNamespace(count_rows=lambda expr: 2)
            self.optimized = False
            self.closed = False

        def rehydrate_from_raw_json(self, *, progress=None):
            if progress is not None:
                progress(2)
            return SimpleNamespace(tweets_updated=2, secondary_records=5)

        def optimize(self) -> None:
            self.optimized = True

        def close(self) -> None:
            self.closed = True

    store = FakeStore()
    monkeypatch.setattr(cli, "open_archive_store", lambda _paths, create=False: store)
    monkeypatch.setattr(
        cli,
        "_with_archive_write_lock",
        lambda lock_paths, fn: (lock_calls.append(lock_paths.lock_file), fn())[1],
    )
    monkeypatch.setitem(sys.modules, "tqdm", SimpleNamespace(tqdm=FakeTqdm))

    cli.rehydrate_archive()

    assert lock_calls == [paths.lock_file]
    assert store.optimized is True
    assert store.closed is True
    assert "rehydrated 2 tweet rows and rebuilt 5 secondary rows" in buffer.getvalue()


def test_embed_archive_uses_write_lock(paths, monkeypatch) -> None:
    buffer = StringIO()
    _capture_console(monkeypatch, buffer)
    monkeypatch.setattr(cli, "load_config", lambda: (AppConfig(), paths))
    lock_calls: list[Path] = []

    class FakeTqdm:
        def __init__(self, *args, **kwargs) -> None:
            self.updates: list[int] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def update(self, count: int) -> None:
            self.updates.append(count)

    class FakeEmbeddingEngine:
        def embed_batch(self, texts):
            return [[0.1, 0.2, 0.3] for _ in texts]

    class FakeStore:
        def __init__(self) -> None:
            self.cleared = False
            self.optimized = False
            self.closed = False
            self.writes: list[tuple[list[dict[str, object]], list[list[float]]]] = []

        def clear_embeddings(self) -> None:
            self.cleared = True

        def count_unembedded(self) -> int:
            return 1

        def get_unembedded_tweets(self, *, batch_size: int = 100):
            return [[{"author_username": "user1", "text": "bookmark tweet"}]]

        def write_embeddings(self, batch, vectors) -> None:
            self.writes.append((batch, vectors))

        def optimize(self) -> None:
            self.optimized = True

        def close(self) -> None:
            self.closed = True

    store = FakeStore()
    monkeypatch.setattr(cli, "open_archive_store", lambda _paths, create=False: store)
    monkeypatch.setattr(
        cli,
        "_with_archive_write_lock",
        lambda lock_paths, fn: (lock_calls.append(lock_paths.lock_file), fn())[1],
    )
    monkeypatch.setitem(sys.modules, "tqdm", SimpleNamespace(tqdm=FakeTqdm))
    monkeypatch.setitem(
        sys.modules,
        "tweetxvault.embed",
        SimpleNamespace(EmbeddingEngine=FakeEmbeddingEngine),
    )

    cli.embed_archive(regen=True)

    assert lock_calls == [paths.lock_file]
    assert store.cleared is True
    assert store.optimized is True
    assert store.closed is True
    assert len(store.writes) == 1
    assert "embedded 1 tweets" in buffer.getvalue()
