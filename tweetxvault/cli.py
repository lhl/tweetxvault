"""Typer CLI entrypoint."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated

import typer
from loguru import logger
from rich.console import Console

from tweetxvault.config import ensure_paths, load_config
from tweetxvault.exceptions import ConfigError, TweetXVaultError
from tweetxvault.export import export_json_archive
from tweetxvault.export.json_export import default_export_path
from tweetxvault.query_ids import QueryIdStore, refresh_query_ids
from tweetxvault.storage import open_archive_store
from tweetxvault.sync import run_preflight, sync_all, sync_collection

app = typer.Typer(no_args_is_help=True)
auth_app = typer.Typer(no_args_is_help=True)
export_app = typer.Typer(no_args_is_help=True)
sync_app = typer.Typer(no_args_is_help=True)

app.add_typer(auth_app, name="auth")
app.add_typer(export_app, name="export")
app.add_typer(sync_app, name="sync")


def _configure_logging() -> Console:
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    return Console(stderr=True)


@sync_app.command("bookmarks")
def sync_bookmarks(full: bool = False, limit: int | None = None) -> None:
    console = _configure_logging()
    try:
        result = asyncio.run(sync_collection("bookmarks", full=full, limit=limit, console=console))
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
def sync_likes(full: bool = False, limit: int | None = None) -> None:
    console = _configure_logging()
    try:
        result = asyncio.run(sync_collection("likes", full=full, limit=limit, console=console))
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
def sync_everything(full: bool = False, limit: int | None = None) -> None:
    console = _configure_logging()
    try:
        outcome = asyncio.run(sync_all(full=full, limit=limit, console=console))
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


@export_app.command("json")
def export_json(
    collection: Annotated[str, typer.Option("--collection")] = "all",
    out: Annotated[Path | None, typer.Option("--out")] = None,
) -> None:
    console = _configure_logging()
    _, paths = load_config()
    store = open_archive_store(paths, create=False)
    if store is None:
        console.print("[red]No local archive found.[/red]")
        raise typer.Exit(1)
    try:
        out_path = out or default_export_path(paths.data_dir / "exports", collection)
        export_json_archive(store, collection=collection, out_path=out_path)
    finally:
        store.close()
    console.print(f"exported {collection} archive to {out_path}")


@app.callback()
def main() -> None:
    """tweetxvault CLI."""
