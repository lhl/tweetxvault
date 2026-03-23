"""Convert pre-2018 Grailbird Twitter archives into modern YTD archives."""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tweetxvault.exceptions import ConfigError

_USER_DETAILS_RE = re.compile(r"var user_details\s*=\s*(\{.*\})\s*;?\s*$", re.DOTALL)
_DEFAULT_CREATED_AT = "2006-01-01 00:00:00 +0000"
_DEFAULT_DISPLAY_NAME = "Unknown User"


@dataclass(slots=True)
class GrailbirdConversionResult:
    output_path: Path
    tweet_count: int
    account_id: str | None
    screen_name: str | None
    full_name: str
    warnings: list[str] = field(default_factory=list)


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def parse_user_details(user_details_path: Path) -> dict[str, str] | None:
    """Parse Grailbird ``user_details.js`` into a minimal account mapping."""
    try:
        content = user_details_path.read_text(encoding="utf-8")
    except OSError:
        return None

    match = _USER_DETAILS_RE.search(content)
    if not match:
        return None

    try:
        normalized = re.sub(r",(\s*[}\]])", r"\1", match.group(1))
        user_data = json.loads(normalized)
    except json.JSONDecodeError:
        return None

    account_id = _normalize_optional_string(user_data.get("id"))
    screen_name = _normalize_optional_string(user_data.get("screen_name"))
    if not account_id or not screen_name:
        return None

    full_name = _normalize_optional_string(user_data.get("full_name")) or _DEFAULT_DISPLAY_NAME
    created_at = _normalize_optional_string(user_data.get("created_at")) or _DEFAULT_CREATED_AT
    return {
        "id": account_id,
        "screen_name": screen_name,
        "full_name": full_name,
        "created_at": created_at,
    }


def parse_grailbird_timestamp(timestamp_str: str) -> datetime | None:
    """Parse Grailbird ``YYYY-mm-dd HH:MM:SS +0000`` timestamps."""
    try:
        return datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None


def parse_timestamp(timestamp_str: str) -> str:
    """Convert Grailbird timestamps into Twitter's archive ``created_at`` format."""
    parsed = parse_grailbird_timestamp(timestamp_str)
    if parsed is None:
        return timestamp_str
    return parsed.strftime("%a %b %d %H:%M:%S %z %Y")


def convert_csv_to_tweet_object(row: dict[str, str]) -> dict[str, Any]:
    """Convert one Grailbird CSV row into the modern YTD tweet object shape."""
    tweet_id = row.get("tweet_id", "")
    timestamp = row.get("timestamp", "")
    text = row.get("text", "")
    source = row.get("source", "")
    in_reply_to_status_id = row.get("in_reply_to_status_id", "")
    in_reply_to_user_id = row.get("in_reply_to_user_id", "")
    retweeted_status_id = row.get("retweeted_status_id", "")
    retweeted_status_user_id = row.get("retweeted_status_user_id", "")
    retweeted_status_timestamp = row.get("retweeted_status_timestamp", "")
    expanded_urls = row.get("expanded_urls", "")

    urls: list[dict[str, Any]] = []
    if expanded_urls:
        seen: set[str] = set()
        for raw_url in re.split(r",(?=https?://)", expanded_urls):
            url = raw_url.strip()
            if not url or url in seen:
                continue
            seen.add(url)
            urls.append(
                {
                    "url": url,
                    "expanded_url": url,
                    "display_url": url,
                    "indices": ["0", "0"],
                }
            )

    tweet = {
        "tweet": {
            "retweeted": bool(retweeted_status_id),
            "source": source,
            "entities": {
                "hashtags": [],
                "symbols": [],
                "user_mentions": [],
                "urls": urls,
            },
            "display_text_range": ["0", str(len(text))],
            "favorite_count": "0",
            "id_str": tweet_id,
            "truncated": False,
            "retweet_count": "0",
            "id": tweet_id,
            "created_at": parse_timestamp(timestamp),
            "favorited": False,
            "full_text": text,
            "lang": "en",
        }
    }

    if in_reply_to_status_id:
        tweet["tweet"]["in_reply_to_status_id_str"] = in_reply_to_status_id
        tweet["tweet"]["in_reply_to_status_id"] = in_reply_to_status_id
    if in_reply_to_user_id:
        tweet["tweet"]["in_reply_to_user_id"] = in_reply_to_user_id
        tweet["tweet"]["in_reply_to_user_id_str"] = in_reply_to_user_id
    if retweeted_status_id:
        tweet["tweet"]["retweeted_status_id_str"] = retweeted_status_id
        tweet["tweet"]["retweeted_status_id"] = retweeted_status_id
    if retweeted_status_user_id:
        tweet["tweet"]["retweeted_status_user_id_str"] = retweeted_status_user_id
        tweet["tweet"]["retweeted_status_user_id"] = retweeted_status_user_id
    if retweeted_status_timestamp:
        tweet["tweet"]["retweeted_status_timestamp"] = parse_timestamp(retweeted_status_timestamp)
    return tweet


def _default_account_info() -> dict[str, str]:
    return {
        "id": "",
        "screen_name": "",
        "full_name": _DEFAULT_DISPLAY_NAME,
        "created_at": _DEFAULT_CREATED_AT,
    }


def _validate_paths(input_path: Path, output_path: Path, *, force: bool) -> tuple[Path, Path]:
    input_resolved = input_path.expanduser().resolve()
    output_expanded = output_path.expanduser()
    output_resolved = output_expanded.resolve(strict=False)

    if not input_resolved.exists():
        raise ConfigError(f"Input directory '{input_path}' does not exist.")
    if not input_resolved.is_dir():
        raise ConfigError(f"Input path '{input_path}' is not a directory.")
    if input_resolved == output_resolved:
        raise ConfigError("Output directory must be different from the input archive directory.")
    if input_resolved.is_relative_to(output_resolved):
        raise ConfigError("Output directory must not contain the input archive directory.")

    if output_expanded.exists():
        if not force:
            raise ConfigError(
                f"Output directory '{output_path}' already exists. Use --force to overwrite it."
            )
        if not output_expanded.is_dir():
            raise ConfigError(f"Output path '{output_path}' exists and is not a directory.")
        shutil.rmtree(output_expanded)

    return input_resolved, output_expanded


def _load_tweets(csv_file: Path) -> list[dict[str, Any]]:
    try:
        with csv_file.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            return [convert_csv_to_tweet_object(row) for row in reader]
    except OSError as exc:
        raise ConfigError(f"Failed to read {csv_file.name}: {exc}") from exc
    except csv.Error as exc:
        raise ConfigError(f"Failed to parse {csv_file.name}: {exc}") from exc


def _write_assignment(path: Path, assignment: str, payload: Any) -> None:
    try:
        with path.open("w", encoding="utf-8") as handle:
            handle.write(f"{assignment} = ")
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except OSError as exc:
        raise ConfigError(f"Failed to write {path}: {exc}") from exc


def convert_archive(
    input_dir: Path | str, output_dir: Path | str, *, force: bool = False
) -> GrailbirdConversionResult:
    """Convert a Grailbird archive directory into a modern YTD archive directory."""
    input_path, output_path = _validate_paths(Path(input_dir), Path(output_dir), force=force)

    csv_file = input_path / "tweets.csv"
    if not csv_file.exists():
        raise ConfigError(f"{csv_file} not found. Expected a Grailbird archive with tweets.csv.")

    warnings: list[str] = []
    user_details_path = input_path / "data" / "js" / "user_details.js"
    account_info = None
    if user_details_path.exists():
        account_info = parse_user_details(user_details_path)
        if account_info is None:
            warnings.append(
                "could not parse data/js/user_details.js; archive owner metadata will stay unset "
                "until a later authenticated sync"
            )
    else:
        warnings.append(
            "archive does not include data/js/user_details.js; archive owner metadata will stay "
            "unset until a later authenticated sync"
        )

    if account_info is None:
        account_info = _default_account_info()

    tweets = _load_tweets(csv_file)
    if not tweets:
        raise ConfigError(f"No tweets found in {csv_file.name}.")

    output_path.mkdir(parents=True, exist_ok=True)
    data_dir = output_path / "data"
    data_dir.mkdir(exist_ok=True)

    _write_assignment(data_dir / "tweets.js", "window.YTD.tweets.part0", tweets)

    created_dt = parse_grailbird_timestamp(account_info["created_at"])
    created_at_iso = (
        created_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        if created_dt is not None
        else "2006-01-01T00:00:00.000Z"
    )
    manifest = {
        "userInfo": {
            "accountId": account_info["id"],
            "userName": account_info["screen_name"],
            "displayName": account_info["full_name"],
        },
        "archiveInfo": {
            "sizeBytes": "0",
            "generationDate": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "isPartialArchive": False,
            "maxPartSizeBytes": "0",
            "sourceFormat": "grailbird",
        },
        "dataTypes": {
            "account": {
                "files": [
                    {
                        "fileName": "data/account.js",
                        "globalName": "YTD.account.part0",
                        "count": "1",
                    }
                ]
            },
            "tweets": {
                "files": [
                    {
                        "fileName": "data/tweets.js",
                        "globalName": "YTD.tweets.part0",
                        "count": str(len(tweets)),
                    }
                ]
            },
        },
    }
    _write_assignment(data_dir / "manifest.js", "window.__THAR_CONFIG", manifest)

    account_payload = [
        {
            "account": {
                "accountId": account_info["id"],
                "username": account_info["screen_name"],
                "accountDisplayName": account_info["full_name"],
                "createdVia": "web",
                "createdAt": created_at_iso,
            }
        }
    ]
    _write_assignment(data_dir / "account.js", "window.YTD.account.part0", account_payload)

    return GrailbirdConversionResult(
        output_path=output_path,
        tweet_count=len(tweets),
        account_id=_normalize_optional_string(account_info["id"]),
        screen_name=_normalize_optional_string(account_info["screen_name"]),
        full_name=account_info["full_name"],
        warnings=warnings,
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Convert old Twitter/X Grailbird archives into the modern YTD format that "
            "tweetxvault can import."
        )
    )
    parser.add_argument("input_dir", help="Path to the Grailbird archive directory.")
    parser.add_argument("output_dir", help="Path to the converted archive directory.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the output directory if it already exists.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    try:
        result = convert_archive(args.input_dir, args.output_dir, force=args.force)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if result.screen_name:
        print(
            "Converted "
            f"{result.tweet_count} tweets for @{result.screen_name} -> {result.output_path}"
        )
    else:
        print(
            f"Converted {result.tweet_count} tweets -> {result.output_path} "
            "(account metadata unavailable)"
        )
    for warning in result.warnings:
        print(f"Warning: {warning}")
    print(f'tweetxvault import x-archive "{result.output_path}"')
    return 0
