"""Typer CLI entrypoint."""

from __future__ import annotations

import asyncio
import resource
import sys
from pathlib import Path
from typing import Annotated

import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

from tweetxvault.config import ensure_paths, load_config
from tweetxvault.exceptions import ConfigError, TweetXVaultError
from tweetxvault.export import export_html_archive, export_json_archive
from tweetxvault.export.common import (
    default_export_path,
    display_collection_name,
    normalize_collection_name,
    tweet_url,
)
from tweetxvault.query_ids import QueryIdStore, refresh_query_ids
from tweetxvault.storage import open_archive_store
from tweetxvault.sync import run_preflight, sync_all, sync_collection

app = typer.Typer(no_args_is_help=True)
auth_app = typer.Typer(no_args_is_help=True)
export_app = typer.Typer(no_args_is_help=True)
sync_app = typer.Typer(no_args_is_help=True)
view_app = typer.Typer(no_args_is_help=True)

app.add_typer(auth_app, name="auth")
app.add_typer(export_app, name="export")
app.add_typer(sync_app, name="sync")
app.add_typer(view_app, name="view")


def _configure_logging() -> Console:
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    return Console(stderr=True)


def _normalize_collection_or_exit(collection: str, console: Console) -> str:
    try:
        return normalize_collection_name(collection)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


def _open_store_for_read(console: Console):
    _, paths = load_config()
    store = open_archive_store(paths, create=False)
    if store is None:
        console.print("[red]No local archive found.[/red]")
        raise typer.Exit(1)
    return store, paths


def _with_auto_optimize(store, console: Console, fn):
    """Run fn(store), auto-optimizing and retrying once on too-many-open-files."""
    try:
        return fn(store)
    except (RuntimeError, OSError) as exc:
        if "Too many open files" not in str(exc):
            raise
        console.print("archive has too many versions, optimizing...")
        store.optimize()
        return fn(store)


def _render_archive_view(
    console: Console, *, collection: str, limit: int, sort: str = "newest"
) -> None:
    normalized = _normalize_collection_or_exit(collection, console)
    store, _ = _open_store_for_read(console)
    try:
        rows = _with_auto_optimize(store, console, lambda s: s.export_rows(normalized, sort=sort))
    finally:
        store.close()

    label = display_collection_name(normalized)
    if not rows:
        console.print(f"[yellow]No archived {label} rows found.[/yellow]")
        return

    shown = rows[:limit]
    table = Table(title=f"{label} archive")
    table.add_column("Created", style="cyan", no_wrap=True)
    table.add_column("Author", style="green", no_wrap=True)
    table.add_column("Text", overflow="fold")
    table.add_column("URL", style="magenta", overflow="fold")

    for row in shown:
        author = row["author"]
        username = author["username"] if author["username"] else author["id"] or "unknown"
        text = (row["text"] or "").replace("\n", " ")
        table.add_row(
            row["created_at"] or "",
            f"@{username}",
            text,
            tweet_url(row),
        )

    console.print(f"showing {len(shown)} of {len(rows)} archived {label} tweets")
    console.print(table)


@sync_app.command("bookmarks")
def sync_bookmarks(full: bool = False, backfill: bool = False, limit: int | None = None) -> None:
    console = _configure_logging()
    try:
        result = asyncio.run(
            sync_collection("bookmarks", full=full, backfill=backfill, limit=limit, console=console)
        )
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetXVaultError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    console.print(
        "bookmarks: "
        f"{result.pages_fetched} pages, "
        f"{result.tweets_seen} tweets, "
        f"{result.stop_reason}"
    )


@sync_app.command("likes")
def sync_likes(full: bool = False, backfill: bool = False, limit: int | None = None) -> None:
    console = _configure_logging()
    try:
        result = asyncio.run(
            sync_collection("likes", full=full, backfill=backfill, limit=limit, console=console)
        )
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetXVaultError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    console.print(
        f"likes: {result.pages_fetched} pages, {result.tweets_seen} tweets, {result.stop_reason}"
    )


@sync_app.command("all")
def sync_everything(full: bool = False, backfill: bool = False, limit: int | None = None) -> None:
    console = _configure_logging()
    try:
        outcome = asyncio.run(sync_all(full=full, backfill=backfill, limit=limit, console=console))
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except TweetXVaultError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    for result in outcome.results:
        console.print(
            f"{result.collection}: {result.pages_fetched} pages, {result.tweets_seen} tweets"
        )
    for collection, error in outcome.errors.items():
        console.print(f"{collection}: failed ({error})")
    raise typer.Exit(outcome.exit_code)


@auth_app.command("check")
def auth_check() -> None:
    console = _configure_logging()
    config, paths = load_config()
    try:
        result = asyncio.run(
            run_preflight(config=config, paths=paths, collections=["bookmarks", "likes"])
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
            console,
            lambda s: export_html_archive(s, collection=normalized, out_path=out_path),
        )
    finally:
        store.close()
    console.print(f"exported {display_collection_name(normalized)} archive to {out_path}")


@app.command("optimize")
def optimize_archive() -> None:
    """Compact the LanceDB archive to reduce file count and reclaim space."""
    console = _configure_logging()
    store, _ = _open_store_for_read(console)
    try:
        before = store.version_count()
        store.optimize()
        after = store.version_count()
        console.print(f"optimized archive: {before} versions -> {after} versions")
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
