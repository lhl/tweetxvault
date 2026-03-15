from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

import tweetxvault.cli as cli
from tweetxvault.auth import BrowserCandidate
from tweetxvault.client.timelines import TimelineTweet
from tweetxvault.config import AppConfig, AuthConfig
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
        lambda config, env=None: SimpleNamespace(
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
