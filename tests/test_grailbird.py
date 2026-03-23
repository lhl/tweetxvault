from __future__ import annotations

import asyncio
import csv
import json
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

import tweetxvault.archive_import as archive_import
from tweetxvault.archive_import import import_x_archive
from tweetxvault.config import AppConfig
from tweetxvault.exceptions import ConfigError
from tweetxvault.grailbird import convert_archive, parse_user_details
from tweetxvault.storage import open_archive_store


def _console() -> Console:
    return Console(file=StringIO(), force_terminal=False, color_system=None)


def _disable_live_reconciliation(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_no_auth(_config):
        raise ConfigError("no auth configured")

    monkeypatch.setattr(archive_import, "resolve_auth_bundle", raise_no_auth)


def _write_grailbird_archive(
    base: Path,
    *,
    include_user_details: bool = True,
) -> Path:
    root = base / "grailbird"
    tweets_dir = root / "data" / "js" / "tweets"
    tweets_dir.mkdir(parents=True)

    with (root / "tweets.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "tweet_id",
                "timestamp",
                "text",
                "source",
                "in_reply_to_status_id",
                "in_reply_to_user_id",
                "retweeted_status_id",
                "retweeted_status_user_id",
                "retweeted_status_timestamp",
                "expanded_urls",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "tweet_id": "100",
                "timestamp": "2015-01-10 20:59:42 +0000",
                "text": "archive tweet with urls",
                "source": '<a href="https://x.com" rel="nofollow">Twitter Web App</a>',
                "in_reply_to_status_id": "",
                "in_reply_to_user_id": "",
                "retweeted_status_id": "",
                "retweeted_status_user_id": "",
                "retweeted_status_timestamp": "",
                "expanded_urls": (
                    "http://www.latimes.com/story/20140113,0,5661959.story,https://example.com/post"
                ),
            }
        )

    (tweets_dir / "2015_01.js").write_text("var tweet_index = [];\n", encoding="utf-8")
    if include_user_details:
        (root / "data" / "js" / "user_details.js").write_text(
            (
                'var user_details={"created_at":"2006-07-14 18:42:26 +0000",'
                '"id":926,"full_name":"Brad Barrish","screen_name":"bradbarrish"};'
            ),
            encoding="utf-8",
        )
    return root


def test_parse_user_details_parses_current_archive_format(tmp_path: Path) -> None:
    path = tmp_path / "user_details.js"
    path.write_text(
        """var user_details =  {
  "expanded_url" : "http:\\/\\/whatevernevermind.com",
  "screen_name" : "bradbarrish",
  "full_name" : "Brad Barrish",
  "id" : "926",
  "created_at" : "2006-07-14 18:42:26 +0000"
}""",
        encoding="utf-8",
    )

    assert parse_user_details(path) == {
        "id": "926",
        "screen_name": "bradbarrish",
        "full_name": "Brad Barrish",
        "created_at": "2006-07-14 18:42:26 +0000",
    }


def test_parse_user_details_ignores_trailing_comma(tmp_path: Path) -> None:
    path = tmp_path / "user_details.js"
    path.write_text(
        """var user_details = {
  "screen_name": "bradbarrish",
  "id": "926",
  "full_name": "Brad Barrish",
  "created_at": "2006-07-14 18:42:26 +0000",
};""",
        encoding="utf-8",
    )

    result = parse_user_details(path)
    assert result is not None
    assert result["created_at"] == "2006-07-14 18:42:26 +0000"


def test_convert_archive_round_trips_into_import_x_archive(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    grailbird_dir = _write_grailbird_archive(tmp_path)
    converted_dir = tmp_path / "converted"
    _disable_live_reconciliation(monkeypatch)

    conversion = convert_archive(grailbird_dir, converted_dir)
    assert conversion.tweet_count == 1
    assert conversion.account_id == "926"
    assert conversion.screen_name == "bradbarrish"

    result = asyncio.run(
        import_x_archive(
            converted_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert result.counts["authored_tweets"] == 1
    assert result.counts["deleted_authored_tweets"] == 0
    assert result.counts["likes"] == 0

    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store.get_archive_owner_id() == "926"
    tweet_row = store.table.search().where("row_key = 'tweet:tweet::100'").limit(1).to_list()[0]
    assert tweet_row["author_username"] == "bradbarrish"
    raw_json = json.loads(tweet_row["raw_json"])
    urls = (raw_json.get("legacy") or {}).get("entities", {}).get("urls", [])
    assert [item["expanded_url"] for item in urls] == [
        "http://www.latimes.com/story/20140113,0,5661959.story",
        "https://example.com/post",
    ]
    store.close()


def test_convert_archive_without_user_details_keeps_owner_unset(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    grailbird_dir = _write_grailbird_archive(tmp_path, include_user_details=False)
    converted_dir = tmp_path / "converted"
    _disable_live_reconciliation(monkeypatch)

    conversion = convert_archive(grailbird_dir, converted_dir)
    assert conversion.account_id is None
    assert conversion.screen_name is None
    assert conversion.warnings

    asyncio.run(
        import_x_archive(
            converted_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store.get_archive_owner_id() is None
    store.ensure_archive_owner_id("42")
    assert store.get_archive_owner_id() == "42"
    tweet_row = store.table.search().where("row_key = 'tweet:tweet::100'").limit(1).to_list()[0]
    assert tweet_row["author_id"] is None
    assert tweet_row["author_username"] is None
    store.close()


def test_convert_archive_requires_force_to_overwrite_output(tmp_path: Path) -> None:
    grailbird_dir = _write_grailbird_archive(tmp_path)
    converted_dir = tmp_path / "converted"
    converted_dir.mkdir()

    with pytest.raises(ConfigError, match="already exists"):
        convert_archive(grailbird_dir, converted_dir)
