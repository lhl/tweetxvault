from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from rich.console import Console

import tweetxvault.cli as cli
from tweetxvault.client.timelines import TimelineTweet
from tweetxvault.config import AppConfig
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
