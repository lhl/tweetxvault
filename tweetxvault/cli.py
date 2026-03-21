"""Typer CLI entrypoint."""

from __future__ import annotations

import asyncio
import os
import re
import resource
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import typer
from loguru import logger
from rich import box
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from tweetxvault.archive_import import enrich_imported_archive, import_x_archive
from tweetxvault.articles import refresh_articles
from tweetxvault.auth import (
    BrowserCandidate,
    list_available_browser_candidates,
    resolve_auth_bundle,
)
from tweetxvault.config import ensure_paths, load_config
from tweetxvault.exceptions import ConfigError, ProcessLockError, TweetXVaultError
from tweetxvault.export import export_html_archive, export_json_archive
from tweetxvault.export.common import (
    default_export_path,
    display_collection_name,
    normalize_collection_name,
)
from tweetxvault.media import download_media
from tweetxvault.query_ids import QueryIdStore, refresh_query_ids
from tweetxvault.storage import open_archive_store
from tweetxvault.sync import ProcessLock, run_preflight, sync_all, sync_collection
from tweetxvault.threads import expand_threads
from tweetxvault.unfurl import unfurl_urls

app = typer.Typer(no_args_is_help=True)
article_app = typer.Typer(no_args_is_help=True)
auth_app = typer.Typer(no_args_is_help=True)
export_app = typer.Typer(no_args_is_help=True)
import_app = typer.Typer(no_args_is_help=True)
media_app = typer.Typer(no_args_is_help=True)
sync_app = typer.Typer(no_args_is_help=True)
thread_app = typer.Typer(no_args_is_help=True)
view_app = typer.Typer(no_args_is_help=True)

app.add_typer(article_app, name="articles")
app.add_typer(auth_app, name="auth")
app.add_typer(export_app, name="export")
app.add_typer(import_app, name="import")
app.add_typer(media_app, name="media")
app.add_typer(sync_app, name="sync")
app.add_typer(thread_app, name="threads")
app.add_typer(view_app, name="view")


BROWSER_HELP = (
    "Browser to use for cookie extraction: firefox, chrome, chromium, brave, edge, "
    "opera, opera-gx, vivaldi, arc."
)
DEBUG_AUTH_HELP = "Print browser/profile auth-resolution diagnostics."
ARTICLE_BACKFILL_HELP = (
    "Rewalk existing timeline pages without resetting sync state so older items can pick up "
    "new article fields."
)
HEAD_ONLY_HELP = (
    "Clear any saved backfill cursor for the targeted collection and run only the head pass. "
    "Does not resume older historical backfill state."
)
SYNC_BROWSER_OPTION = Annotated[str | None, typer.Option("--browser", help=BROWSER_HELP)]
SYNC_PROFILE_OPTION = Annotated[
    str | None,
    typer.Option("--profile", help="Browser profile name or directory name."),
]
SYNC_PROFILE_PATH_OPTION = Annotated[
    Path | None,
    typer.Option("--profile-path", help="Explicit browser profile directory path."),
]
SYNC_ARTICLE_BACKFILL_OPTION = Annotated[
    bool,
    typer.Option("--article-backfill", help=ARTICLE_BACKFILL_HELP),
]
SYNC_HEAD_ONLY_OPTION = Annotated[
    bool,
    typer.Option("--head-only", help=HEAD_ONLY_HELP),
]
DEBUG_AUTH_OPTION = Annotated[
    bool,
    typer.Option("--debug-auth", help=DEBUG_AUTH_HELP),
]
SEARCH_TYPE_HELP = "Comma-delimited search result types: post, article."
SEARCH_COLLECTION_HELP = "Comma-delimited collections: bookmark, like, tweet."
SEARCH_SORT_HELP = "Search result sort: relevance, newest, oldest."
# Keep the user-facing flag as --type, but map it onto internal search-result kinds so
# search code does not collide with storage-level record_type/type terminology.
SEARCH_TYPE_ALIASES = {
    "post": "post",
    "posts": "post",
    "article": "article",
    "articles": "article",
}
SEARCH_SORT_OPTION = Annotated[
    Literal["relevance", "newest", "oldest"],
    typer.Option("--sort", help=SEARCH_SORT_HELP),
]


def _configure_logging() -> Console:
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    return Console(stderr=True)


def _browser_cookie_only_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in ("TWEETXVAULT_AUTH_TOKEN", "TWEETXVAULT_CT0"):
        env.pop(key, None)
    return env


def _auth_status_callback(console: Console, *, enabled: bool):
    if not enabled:
        return None
    return lambda message: console.print(f"auth: {message}", highlight=False)


def _pick_browser_candidate_interactively(
    console: Console,
    *,
    browser: str | None,
) -> BrowserCandidate:
    candidates = list_available_browser_candidates(browser=browser)
    if not candidates:
        scope = f" for {browser}" if browser else ""
        raise ConfigError(f"No browser profiles with X session cookies were found{scope}.")

    table = Table(title="Browser profiles with X cookies", box=box.HORIZONTALS)
    table.add_column("#", no_wrap=True, style="cyan")
    table.add_column("Browser", style="green", no_wrap=True)
    table.add_column("Profile", no_wrap=True)
    table.add_column("Path", overflow="fold")
    table.add_column("Tags", no_wrap=True)
    for index, candidate in enumerate(candidates, start=1):
        table.add_row(
            str(index),
            candidate.browser_name,
            candidate.profile_name,
            str(candidate.profile_path),
            candidate.tags,
        )
    console.print(table)
    choice = Prompt.ask(
        "Choose browser profile",
        choices=[str(index) for index in range(1, len(candidates) + 1)],
        default="1",
        console=console,
    )
    return candidates[int(choice) - 1]


def _prepare_auth_override(
    config,
    console: Console,
    *,
    browser: str | None,
    profile: str | None,
    profile_path: Path | None,
    debug_auth: bool = False,
    interactive: bool = False,
):
    if interactive and (profile or (profile_path is not None)):
        raise ConfigError("--interactive cannot be combined with --profile or --profile-path.")
    if interactive:
        candidate = _pick_browser_candidate_interactively(console, browser=browser)
        browser = candidate.browser_id
        profile = None
        profile_path = candidate.profile_path

    if (profile or (profile_path is not None)) and not browser:
        raise ConfigError("--profile and --profile-path require --browser.")
    if not browser:
        return config, None

    auth = config.auth.model_copy(
        update={
            "auth_token": None,
            "ct0": None,
            "browser": browser,
            "browser_profile": profile,
            "browser_profile_path": str(profile_path) if profile_path is not None else None,
            "firefox_profile_path": None,
        }
    )
    forced_config = config.model_copy(update={"auth": auth})
    auth_bundle = resolve_auth_bundle(
        forced_config,
        env=_browser_cookie_only_env(),
        status=_auth_status_callback(console, enabled=debug_auth),
    )
    return forced_config, auth_bundle


def _normalize_collection_or_exit(collection: str, console: Console) -> str:
    try:
        return normalize_collection_name(collection)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


def _parse_search_types(value: str | None, console: Console) -> set[str] | None:
    if value is None:
        return None
    normalized: set[str] = set()
    invalid: list[str] = []
    for raw_part in value.split(","):
        part = raw_part.strip().lower()
        if not part:
            continue
        search_type = SEARCH_TYPE_ALIASES.get(part)
        if search_type is None:
            invalid.append(raw_part.strip())
            continue
        normalized.add(search_type)
    if invalid:
        allowed = ", ".join(sorted(SEARCH_TYPE_ALIASES))
        console.print(
            f"[red]Unsupported search type(s): {', '.join(invalid)}. "
            f"Expected one of: {allowed}.[/red]"
        )
        raise typer.Exit(1)
    return normalized or None


def _parse_search_collections(value: str | None, console: Console) -> set[str] | None:
    if value is None:
        return None
    normalized: set[str] = set()
    invalid: list[str] = []
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        try:
            normalized.add(normalize_collection_name(part))
        except ValueError:
            invalid.append(part)
    if invalid:
        allowed = ", ".join(sorted({"bookmark", "bookmarks", "like", "likes", "tweet", "tweets"}))
        console.print(
            f"[red]Unsupported collection(s): {', '.join(invalid)}. "
            f"Expected one of: {allowed}.[/red]"
        )
        raise typer.Exit(1)
    return normalized or None


def _open_store_for_read(console: Console):
    _, paths = load_config()
    store = open_archive_store(paths, create=False)
    if store is None:
        console.print("[red]No local archive found.[/red]")
        raise typer.Exit(1)
    return store, paths


def _with_archive_write_lock(paths, fn):
    lock = ProcessLock(paths.lock_file)
    lock.acquire()
    try:
        return fn()
    finally:
        lock.release()


def _with_auto_optimize(store, paths, console: Console, fn):
    """Run fn(store), auto-optimizing and retrying once on too-many-open-files."""
    try:
        return fn(store)
    except (RuntimeError, OSError) as exc:
        if "Too many open files" not in str(exc):
            raise
        try:

            def optimize_archive() -> None:
                console.print("archive has too many versions, optimizing...")
                store.optimize()

            _with_archive_write_lock(paths, optimize_archive)
        except ProcessLockError as lock_exc:
            console.print(f"[red]{lock_exc}[/red]")
            console.print("[red]Archive optimize is blocked while another job is writing.[/red]")
            raise typer.Exit(2) from lock_exc
        return fn(store)


def _print_archive_followup(console: Console, result: Any) -> None:
    if result.reconciled_collections:
        console.print(
            "live reconciliation: " + ", ".join(result.reconciled_collections),
            highlight=False,
        )
    console.print(
        "detail enrichment: "
        f"{result.detail_lookups} refreshed, "
        f"{result.detail_terminal_unavailable} terminal, "
        f"{result.detail_transient_failures} transient failures, "
        f"{result.pending_enrichment} pending"
    )
    for warning in result.warnings:
        console.print(f"[yellow]{warning}[/yellow]")


def _run_sync_command(
    *,
    browser: str | None,
    profile: str | None,
    profile_path: Path | None,
    runner: Callable[[Any, Any, Console], Awaitable[Any]],
) -> tuple[Console, Any]:
    console = _configure_logging()
    try:
        config, _ = load_config()
        config, auth_bundle = _prepare_auth_override(
            config,
            console,
            browser=browser,
            profile=profile,
            profile_path=profile_path,
        )
        return console, asyncio.run(runner(config, auth_bundle, console))
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetXVaultError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


def _register_sync_collection_command(collection: str):
    def command(
        full: bool = False,
        backfill: bool = False,
        article_backfill: SYNC_ARTICLE_BACKFILL_OPTION = False,
        head_only: SYNC_HEAD_ONLY_OPTION = False,
        limit: int | None = None,
        browser: SYNC_BROWSER_OPTION = None,
        profile: SYNC_PROFILE_OPTION = None,
        profile_path: SYNC_PROFILE_PATH_OPTION = None,
    ) -> None:
        console, result = _run_sync_command(
            browser=browser,
            profile=profile,
            profile_path=profile_path,
            runner=lambda config, auth_bundle, runner_console: sync_collection(
                collection,
                full=full,
                backfill=backfill,
                article_backfill=article_backfill,
                head_only=head_only,
                limit=limit,
                config=config,
                auth_bundle=auth_bundle,
                console=runner_console,
            ),
        )
        console.print(
            f"{collection}: {result.pages_fetched} pages, {result.tweets_seen} tweets, "
            f"{result.stop_reason}"
        )

    command.__name__ = f"sync_{collection}"
    return sync_app.command(collection)(command)


def _format_created_at(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        dt = datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
        local_dt = dt.astimezone()
        date_part = local_dt.strftime("%b %-d, %Y")
        time_part = local_dt.strftime("%-I:%M %p").lower()
        return f"{date_part}\n{time_part}"
    except (ValueError, TypeError):
        return raw


def _parse_created_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
    except (ValueError, TypeError):
        return None


def _search_result_score(row: dict[str, Any]) -> float:
    score = row.get("match_score")
    try:
        return float(score)
    except (TypeError, ValueError):
        return float("-inf")


def _sort_search_results(rows: list[dict[str, Any]], *, sort: str) -> list[dict[str, Any]]:
    if sort == "relevance":
        return rows

    def sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        created_at = _parse_created_at(row.get("created_at"))
        score_key = -_search_result_score(row)
        tweet_id = str(row.get("tweet_id") or "")
        if created_at is None:
            return (1, 0.0, score_key, tweet_id)
        timestamp = created_at.timestamp()
        if sort == "oldest":
            return (0, timestamp, score_key, tweet_id)
        return (0, -timestamp, score_key, tweet_id)

    return sorted(rows, key=sort_key)


@dataclass(slots=True)
class _TweetListRow:
    tweet_id: str | None
    created_at: str | None
    author_username: str | None
    author_id: str | None
    text: Text
    match: str | None = None
    score: str | None = None


def _format_tweet_text(raw: str | None, *, highlight_query: str | None = None) -> Text:
    text = (raw or "").replace("\n", " ")
    rendered = _highlight_search_matches(text, highlight_query) if highlight_query else Text(text)
    if len(rendered.plain) > 280:
        rendered.truncate(280, overflow="ellipsis")
    return rendered


def _tweet_row_url(row: _TweetListRow) -> str:
    if row.author_username and row.tweet_id:
        return f"https://x.com/{row.author_username}/status/{row.tweet_id}"
    return f"https://x.com/i/web/status/{row.tweet_id or ''}"


def _render_tweet_list(
    console: Console,
    *,
    title: str,
    rows: list[_TweetListRow],
    count_line: str | None = None,
) -> None:
    include_match = any(row.match is not None for row in rows)
    table = Table(
        title=title,
        box=box.HORIZONTALS,
        show_lines=True,
    )
    if include_match:
        table.add_column("Match", style="yellow", no_wrap=True)
    table.add_column("Created", style="cyan", no_wrap=True)
    table.add_column("Author", style="green", no_wrap=True)
    table.add_column("Text", overflow="fold")
    table.add_column("URL", style="magenta", overflow="fold")

    for row in rows:
        username = row.author_username or row.author_id or "unknown"
        values: list[Any] = []
        if include_match:
            values.append(row.match or (row.score or ""))
        values.extend(
            [
                _format_created_at(row.created_at),
                f"@{username}",
                row.text,
                _tweet_row_url(row),
            ]
        )
        table.add_row(*values)

    if count_line:
        console.print(count_line)
    console.print(table)


def _render_archive_view(
    console: Console, *, collection: str, limit: int, sort: str = "newest"
) -> None:
    normalized = _normalize_collection_or_exit(collection, console)
    store, paths = _open_store_for_read(console)
    try:
        total_rows = store.count_export_rows(normalized)
        rows = _with_auto_optimize(
            store,
            paths,
            console,
            lambda s: s.export_rows(
                normalized,
                sort=sort,
                limit=limit,
                include_raw_json=False,
            ),
        )
    finally:
        store.close()

    label = display_collection_name(normalized)
    if not rows:
        console.print(f"[yellow]No archived {label} rows found.[/yellow]")
        return

    display_rows = [
        _TweetListRow(
            tweet_id=row.get("tweet_id"),
            created_at=row.get("created_at"),
            author_username=(row.get("author") or {}).get("username"),
            author_id=(row.get("author") or {}).get("id"),
            text=_format_tweet_text(row.get("text")),
        )
        for row in rows
    ]
    _render_tweet_list(
        console,
        title=f"{label} archive",
        rows=display_rows,
        count_line=f"showing {len(rows)} of {total_rows} archived {label} tweets",
    )


def _highlight_search_matches(text: str, query: str) -> Text:
    rendered = Text(text)
    tokens = [token for token in dict.fromkeys(query.split()) if token]
    if not tokens:
        return rendered
    pattern = re.compile("|".join(re.escape(token) for token in tokens), re.IGNORECASE)
    for match in pattern.finditer(text):
        rendered.stylize("black on yellow", match.start(), match.end())
    return rendered


sync_bookmarks = _register_sync_collection_command("bookmarks")
sync_likes = _register_sync_collection_command("likes")
sync_tweets = _register_sync_collection_command("tweets")


@sync_app.command("all")
def sync_everything(
    full: bool = False,
    backfill: bool = False,
    article_backfill: SYNC_ARTICLE_BACKFILL_OPTION = False,
    head_only: SYNC_HEAD_ONLY_OPTION = False,
    limit: int | None = None,
    browser: SYNC_BROWSER_OPTION = None,
    profile: SYNC_PROFILE_OPTION = None,
    profile_path: SYNC_PROFILE_PATH_OPTION = None,
) -> None:
    console, outcome = _run_sync_command(
        browser=browser,
        profile=profile,
        profile_path=profile_path,
        runner=lambda config, auth_bundle, runner_console: sync_all(
            full=full,
            backfill=backfill,
            article_backfill=article_backfill,
            head_only=head_only,
            limit=limit,
            config=config,
            auth_bundle=auth_bundle,
            console=runner_console,
        ),
    )
    for result in outcome.results:
        console.print(
            f"{result.collection}: {result.pages_fetched} pages, {result.tweets_seen} tweets"
        )
    for collection, error in outcome.errors.items():
        console.print(f"{collection}: failed ({error})")
    raise typer.Exit(outcome.exit_code)


@auth_app.command("check")
def auth_check(
    browser: Annotated[str | None, typer.Option("--browser", help=BROWSER_HELP)] = None,
    profile: Annotated[
        str | None,
        typer.Option("--profile", help="Browser profile name or directory name."),
    ] = None,
    profile_path: Annotated[
        Path | None,
        typer.Option("--profile-path", help="Explicit browser profile directory path."),
    ] = None,
    interactive: Annotated[
        bool,
        typer.Option("--interactive", help="Interactively choose a browser profile."),
    ] = False,
    debug_auth: DEBUG_AUTH_OPTION = False,
) -> None:
    console = _configure_logging()
    config, paths = load_config()
    try:
        config, auth_bundle = _prepare_auth_override(
            config,
            console,
            browser=browser,
            profile=profile,
            profile_path=profile_path,
            debug_auth=debug_auth,
            interactive=interactive,
        )
        if auth_bundle is None:
            auth_bundle = resolve_auth_bundle(
                config,
                status=_auth_status_callback(console, enabled=debug_auth),
            )
        result = asyncio.run(
            run_preflight(
                config=config,
                paths=paths,
                collections=["bookmarks", "likes", "tweets"],
                auth_bundle=auth_bundle,
            )
        )
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetXVaultError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    console.print(
        f"local auth: auth_token={result.auth.auth_token_source}, ct0={result.auth.ct0_source}, "
        f"user_id={result.auth.user_id_source or 'missing'}"
    )
    for collection, probe in result.probes.items():
        status = "ready" if probe.ready else "not ready"
        console.print(f"{collection}: {status} ({probe.detail})")
    if result.has_local_error:
        raise typer.Exit(1)
    if result.has_remote_error:
        raise typer.Exit(2)


@auth_app.command("refresh-ids")
def auth_refresh_ids() -> None:
    console = _configure_logging()
    _, paths = load_config()
    ensure_paths(paths)
    store = QueryIdStore(paths)

    async def _refresh() -> None:
        await refresh_query_ids(store)

    try:
        asyncio.run(_refresh())
    except TweetXVaultError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    cache = store.load()
    console.print(f"refreshed {len(cache.ids)} query IDs into {store.path}")


@article_app.command("refresh")
def refresh_archived_articles(
    targets: Annotated[
        list[str] | None,
        typer.Argument(help="Tweet IDs or x.com status URLs to refresh."),
    ] = None,
    all_articles: Annotated[
        bool,
        typer.Option(
            "--all", help="Refresh all archived article rows, not just preview-only ones."
        ),
    ] = False,
    limit: int | None = None,
    browser: Annotated[str | None, typer.Option("--browser", help=BROWSER_HELP)] = None,
    profile: Annotated[
        str | None,
        typer.Option("--profile", help="Browser profile name or directory name."),
    ] = None,
    profile_path: Annotated[
        Path | None,
        typer.Option("--profile-path", help="Explicit browser profile directory path."),
    ] = None,
) -> None:
    console = _configure_logging()
    try:
        if all_articles and targets:
            raise ConfigError("--all cannot be combined with explicit article targets.")
        config, paths = load_config()
        config, auth_bundle = _prepare_auth_override(
            config,
            console,
            browser=browser,
            profile=profile,
            profile_path=profile_path,
        )
        result = asyncio.run(
            refresh_articles(
                targets=targets,
                preview_only=not all_articles,
                limit=limit,
                config=config,
                paths=paths,
                auth_bundle=auth_bundle,
                console=console,
            )
        )
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetXVaultError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    console.print(
        "articles: "
        f"{result.processed} processed, "
        f"{result.updated} refreshed, "
        f"{result.failed} failed"
    )


@thread_app.command("expand")
def expand_archive_threads(
    targets: Annotated[
        list[str] | None,
        typer.Argument(help="Tweet IDs or x.com status URLs to expand."),
    ] = None,
    limit: int | None = None,
    browser: Annotated[str | None, typer.Option("--browser", help=BROWSER_HELP)] = None,
    profile: Annotated[
        str | None,
        typer.Option("--profile", help="Browser profile name or directory name."),
    ] = None,
    profile_path: Annotated[
        Path | None,
        typer.Option("--profile-path", help="Explicit browser profile directory path."),
    ] = None,
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            help="Re-fetch explicit thread targets even if they were already expanded.",
        ),
    ] = False,
    debug_auth: DEBUG_AUTH_OPTION = False,
) -> None:
    console = _configure_logging()
    try:
        config, paths = load_config()
        config, auth_bundle = _prepare_auth_override(
            config,
            console,
            browser=browser,
            profile=profile,
            profile_path=profile_path,
            debug_auth=debug_auth,
        )
        result = asyncio.run(
            expand_threads(
                targets=targets,
                limit=limit,
                refresh=refresh,
                config=config,
                paths=paths,
                auth_bundle=auth_bundle,
                auth_status=_auth_status_callback(console, enabled=debug_auth),
                console=console,
            )
        )
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetXVaultError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    console.print(
        "threads: "
        f"{result.processed} processed, "
        f"{result.expanded} expanded, "
        f"{result.skipped} skipped, "
        f"{result.failed} failed"
    )


@view_app.command("bookmarks")
def view_bookmarks(limit: int = 20, sort: str = "newest") -> None:
    console = _configure_logging()
    _render_archive_view(console, collection="bookmarks", limit=limit, sort=sort)


@view_app.command("likes")
def view_likes(limit: int = 20, sort: str = "newest") -> None:
    console = _configure_logging()
    _render_archive_view(console, collection="likes", limit=limit, sort=sort)


@view_app.command("all")
def view_all(limit: int = 20, sort: str = "newest") -> None:
    console = _configure_logging()
    _render_archive_view(console, collection="all", limit=limit, sort=sort)


@view_app.command("tweets")
def view_tweets(limit: int = 20, sort: str = "newest") -> None:
    console = _configure_logging()
    _render_archive_view(console, collection="tweets", limit=limit, sort=sort)


@export_app.command("json")
def export_json(
    collection: Annotated[str, typer.Option("--collection")] = "all",
    out: Annotated[Path | None, typer.Option("--out")] = None,
) -> None:
    console = _configure_logging()
    normalized = _normalize_collection_or_exit(collection, console)
    store, paths = _open_store_for_read(console)
    try:
        out_path = out or default_export_path(
            paths.data_dir / "exports",
            normalized,
            extension="json",
        )
        _with_auto_optimize(
            store,
            paths,
            console,
            lambda s: export_json_archive(s, collection=normalized, out_path=out_path),
        )
    finally:
        store.close()
    console.print(f"exported {display_collection_name(normalized)} archive to {out_path}")


@export_app.command("html")
def export_html(
    collection: Annotated[str, typer.Option("--collection")] = "all",
    out: Annotated[Path | None, typer.Option("--out")] = None,
) -> None:
    console = _configure_logging()
    normalized = _normalize_collection_or_exit(collection, console)
    store, paths = _open_store_for_read(console)
    try:
        out_path = out or default_export_path(
            paths.data_dir / "exports",
            normalized,
            extension="html",
        )
        _with_auto_optimize(
            store,
            paths,
            console,
            lambda s: export_html_archive(s, collection=normalized, out_path=out_path),
        )
    finally:
        store.close()
    console.print(f"exported {display_collection_name(normalized)} archive to {out_path}")


@import_app.command("x-archive")
def import_x_archive_command(
    archive: Annotated[
        Path, typer.Argument(help="Path to an X archive zip or extracted directory.")
    ],
    regen: Annotated[
        bool,
        typer.Option(
            "--regen",
            help=(
                "Clear archive-import-owned rows, manifests, and imported media files before "
                "reimporting. Live-synced rows are kept."
            ),
        ),
    ] = False,
    enrich: Annotated[
        bool,
        typer.Option(
            "--enrich",
            help=(
                "Fetch TweetDetail for all pending sparse archive tweets after bulk live syncs. "
                "If this archive was already imported, reuse the existing import and run only "
                "the follow-up enrichment."
            ),
        ),
    ] = False,
    detail_lookups: Annotated[
        int,
        typer.Option(
            "--detail-lookups",
            min=0,
            help=(
                "Maximum number of pending sparse tweets to enrich by fetching X's "
                "per-tweet TweetDetail API after bulk live syncs."
            ),
        ),
    ] = 0,
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit",
            min=1,
            help=(
                "Debug/sample mode: import at most N authored tweets, deleted tweets, likes, "
                "and media files after full dataset load. Requires --debug."
            ),
        ),
    ] = None,
    debug: Annotated[
        bool,
        typer.Option(
            "--debug",
            help=(
                "Print detailed archive-import timing diagnostics. Interactive TTY runs already "
                "show tqdm-based progress bars by default."
            ),
        ),
    ] = False,
    browser: SYNC_BROWSER_OPTION = None,
    profile: SYNC_PROFILE_OPTION = None,
    profile_path: SYNC_PROFILE_PATH_OPTION = None,
    debug_auth: DEBUG_AUTH_OPTION = False,
) -> None:
    console = _configure_logging()
    try:
        config, paths = load_config()
        config, auth_bundle = _prepare_auth_override(
            config,
            console,
            browser=browser,
            profile=profile,
            profile_path=profile_path,
            debug_auth=debug_auth,
        )
        result = asyncio.run(
            import_x_archive(
                archive,
                regen=regen,
                enrich=enrich,
                detail_lookups=detail_lookups,
                limit=limit,
                debug=debug,
                config=config,
                paths=paths,
                auth_bundle=auth_bundle,
                console=console,
            )
        )
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetXVaultError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    if result.skipped and not result.followup_performed:
        console.print("archive import skipped: already imported")
        return
    if result.skipped:
        console.print(
            "archive import: already present; keeping existing imported data "
            "and running follow-up enrichment",
            highlight=False,
        )

    console.print(
        "archive import: "
        f"{result.counts.get('authored_tweets', 0)} authored, "
        f"{result.counts.get('deleted_authored_tweets', 0)} deleted authored, "
        f"{result.counts.get('likes', 0)} likes, "
        f"{result.counts.get('media_files_copied', 0)} media files copied"
    )
    _print_archive_followup(console, result)


@import_app.command("enrich")
def import_archive_enrich(
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit",
            min=1,
            help=(
                "Maximum number of pending sparse archive tweets to enrich via X's "
                "per-tweet TweetDetail API after bulk live syncs."
            ),
        ),
    ] = None,
    browser: SYNC_BROWSER_OPTION = None,
    profile: SYNC_PROFILE_OPTION = None,
    profile_path: SYNC_PROFILE_PATH_OPTION = None,
    debug_auth: DEBUG_AUTH_OPTION = False,
) -> None:
    console = _configure_logging()
    try:
        config, paths = load_config()
        config, auth_bundle = _prepare_auth_override(
            config,
            console,
            browser=browser,
            profile=profile,
            profile_path=profile_path,
            debug_auth=debug_auth,
        )
        result = asyncio.run(
            enrich_imported_archive(
                limit=limit,
                config=config,
                paths=paths,
                auth_bundle=auth_bundle,
                console=console,
            )
        )
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetXVaultError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    console.print("archive enrich: existing imported archive data", highlight=False)
    _print_archive_followup(console, result)


@media_app.command("download")
def media_download(
    limit: int | None = None,
    photos_only: bool = False,
    retry_failed: bool = False,
) -> None:
    console = _configure_logging()
    try:
        config, paths = load_config()
        result = asyncio.run(
            download_media(
                limit=limit,
                photos_only=photos_only,
                retry_failed=retry_failed,
                config=config,
                paths=paths,
                console=console,
            )
        )
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetXVaultError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    console.print(
        "media: "
        f"{result.processed} processed, "
        f"{result.downloaded} downloaded, "
        f"{result.skipped} skipped, "
        f"{result.failed} failed"
    )


@app.command("unfurl")
def unfurl_archive(limit: int | None = None, retry_failed: bool = False) -> None:
    console = _configure_logging()
    try:
        config, paths = load_config()
        result = asyncio.run(
            unfurl_urls(
                limit=limit,
                retry_failed=retry_failed,
                config=config,
                paths=paths,
                console=console,
            )
        )
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetXVaultError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    console.print(
        f"unfurl: {result.processed} processed, {result.updated} updated, {result.failed} failed"
    )


@app.command("optimize")
def optimize_archive() -> None:
    """Compact the LanceDB archive to reduce file count and reclaim space."""
    console = _configure_logging()
    _, paths = load_config()

    def run() -> None:
        store = open_archive_store(paths, create=False)
        if store is None:
            console.print("[red]No local archive found.[/red]")
            raise typer.Exit(1)
        try:
            before = store.version_count()
            console.print(f"compacting {before} versions...")
            store.optimize()
            after = store.version_count()
            console.print(f"optimized archive: {before} versions -> {after} versions")
        finally:
            store.close()

    try:
        _with_archive_write_lock(paths, run)
    except ProcessLockError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


@app.command("rehydrate")
def rehydrate_archive() -> None:
    """Rebuild normalized tweet fields and secondary rows from stored raw_json."""
    from tqdm import tqdm

    console = _configure_logging()
    _, paths = load_config()

    def run() -> None:
        store = open_archive_store(paths, create=False)
        if store is None:
            console.print("[red]No local archive found.[/red]")
            raise typer.Exit(1)
        try:
            total = store.table.count_rows("record_type = 'tweet'")
            if total == 0:
                console.print("archive has no tweet rows")
                return
            with tqdm(total=total, desc="rehydrating", unit="tweets") as pbar:
                result = store.rehydrate_from_raw_json(progress=pbar.update)
            if result.tweets_updated or result.secondary_records:
                console.print("compacting archive...")
                store.optimize()
            console.print(
                f"rehydrated {result.tweets_updated} tweet rows and rebuilt "
                f"{result.secondary_records} secondary rows"
            )
        finally:
            store.close()

    try:
        _with_archive_write_lock(paths, run)
    except ProcessLockError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


@app.command("embed")
def embed_archive(regen: bool = False) -> None:
    """Generate embeddings for archived tweets. Resumes by default."""
    from tqdm import tqdm

    from tweetxvault.embed import EmbeddingEngine

    console = _configure_logging()
    _, paths = load_config()

    def run() -> None:
        store = open_archive_store(paths, create=False)
        if store is None:
            console.print("[red]No local archive found.[/red]")
            raise typer.Exit(1)
        try:
            if regen:
                console.print("clearing existing embeddings...")
                store.clear_embeddings()
            remaining = store.count_unembedded()
            if remaining == 0:
                console.print("all tweets already have embeddings")
                return
            console.print("loading embedding model...")
            engine = EmbeddingEngine()
            console.print(f"embedding {remaining} tweets...")
            batches = store.get_unembedded_tweets(batch_size=100)
            with tqdm(total=remaining, desc="embedding", unit="tweets") as pbar:
                for batch in batches:
                    texts = [
                        f"@{row['author_username'] or ''}: {row['text'] or ''}" for row in batch
                    ]
                    vectors = engine.embed_batch(texts)
                    store.write_embeddings(batch, vectors)
                    pbar.update(len(batch))
            console.print("compacting archive...")
            store.optimize()
            console.print(f"embedded {remaining} tweets")
        finally:
            store.close()

    try:
        _with_archive_write_lock(paths, run)
    except ProcessLockError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


@app.command("search")
def search_archive(
    query: str,
    limit: int = 20,
    mode: str = "auto",
    sort: SEARCH_SORT_OPTION = "relevance",
    type_filter: Annotated[str | None, typer.Option("--type", help=SEARCH_TYPE_HELP)] = None,
    collection_filter: Annotated[
        str | None, typer.Option("--collection", help=SEARCH_COLLECTION_HELP)
    ] = None,
) -> None:
    """Search archived posts and articles. Modes: auto, fts, vector, hybrid."""
    console = _configure_logging()
    store, paths = _open_store_for_read(console)
    try:
        search_types = _parse_search_types(type_filter, console)
        search_collections = _parse_search_collections(collection_filter, console)
        has_vec = store.has_embeddings()
        include_articles = search_types is None or "article" in search_types
        if mode == "auto":
            mode = "hybrid" if has_vec and search_types == {"post"} else "fts"
        if mode in ("vector", "hybrid") and include_articles:
            console.print(
                "[yellow]Semantic search currently supports posts only. "
                "Falling back to full-text search so articles stay included.[/yellow]"
            )
            mode = "fts"
        if mode in ("vector", "hybrid") and not has_vec:
            console.print("[yellow]No embeddings found. Run 'tweetxvault embed' first.[/yellow]")
            console.print("Falling back to full-text search.")
            mode = "fts"

        if mode == "fts":
            results = _with_auto_optimize(
                store,
                paths,
                console,
                lambda s: s.search_fts(
                    query,
                    limit=limit,
                    types=search_types,
                    collections=search_collections,
                ),
            )
        elif mode == "vector":
            from tweetxvault.embed import EmbeddingEngine

            engine = EmbeddingEngine()
            vec = engine.embed_batch([query])[0].tolist()
            results = store.search_vector(vec, limit=limit, collections=search_collections)
        else:
            from tweetxvault.embed import EmbeddingEngine

            engine = EmbeddingEngine()
            vec = engine.embed_batch([query])[0].tolist()
            results = _with_auto_optimize(
                store,
                paths,
                console,
                lambda s: s.search_hybrid(
                    query,
                    vec,
                    limit=limit,
                    collections=search_collections,
                ),
            )

        results = _sort_search_results(results, sort=sort)
        if not results:
            console.print("[yellow]No results found.[/yellow]")
            return

        display_rows: list[_TweetListRow] = []
        for row in results:
            score = row.get("match_score")
            if isinstance(score, float):
                score = f"{score:.3f}"
            type_label = row.get("type") or "post"
            collections = row.get("collections") or []
            match_label = str(type_label)
            if collections:
                match_label = f"{match_label} · {','.join(str(item) for item in collections)}"
            if score not in (None, ""):
                match_label = f"{match_label}\n{score}"
            display_rows.append(
                _TweetListRow(
                    tweet_id=row.get("tweet_id"),
                    created_at=row.get("created_at"),
                    author_username=row.get("author_username"),
                    author_id=row.get("author_id"),
                    text=_format_tweet_text(row.get("text"), highlight_query=query),
                    match=match_label,
                    score=str(score) if score not in (None, "") else None,
                )
            )
        _render_tweet_list(
            console,
            title=f"search: {query}",
            rows=display_rows,
            count_line=f"showing {len(display_rows)} search results",
        )
    finally:
        store.close()


def _raise_nofile_limit() -> None:
    """Raise the soft file-descriptor limit to the hard limit.

    LanceDB creates one table version per merge_insert. Large archives can
    accumulate thousands of versions, each backed by data files that Lance
    opens during queries. The default soft limit (often 1024) is too low.
    """
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft < hard:
        resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))


@app.callback()
def main() -> None:
    """tweetxvault CLI."""
    _raise_nofile_limit()
