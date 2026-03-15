"""Typer CLI entrypoint."""

from __future__ import annotations

import asyncio
import resource
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from loguru import logger
from rich import box
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
    table = Table(
        title=f"{label} archive",
        box=box.HORIZONTALS,
        show_lines=True,
    )
    table.add_column("Created", style="cyan", no_wrap=True)
    table.add_column("Author", style="green", no_wrap=True)
    table.add_column("Text", overflow="fold")
    table.add_column("URL", style="magenta", overflow="fold")

    for row in shown:
        author = row["author"]
        username = author["username"] if author["username"] else author["id"] or "unknown"
        text = (row["text"] or "").replace("\n", " ")
        table.add_row(
            _format_created_at(row["created_at"]),
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
        console.print(f"compacting {before} versions...")
        store.optimize()
        after = store.version_count()
        console.print(f"optimized archive: {before} versions -> {after} versions")
    finally:
        store.close()


@app.command("rehydrate")
def rehydrate_archive() -> None:
    """Re-extract author info from stored raw_json for tweets missing usernames."""
    from tqdm import tqdm

    console = _configure_logging()
    store, _ = _open_store_for_read(console)
    try:
        total = store.table.count_rows("record_type = 'tweet' AND author_username IS NULL")
        if total == 0:
            console.print("all tweets already have author data")
            return
        with tqdm(total=total, desc="rehydrating", unit="tweets") as pbar:
            count = store.rehydrate_authors(progress=pbar.update)
        if count:
            console.print("compacting archive...")
            store.optimize()
        console.print(f"rehydrated author data for {count} tweets")
    finally:
        store.close()


@app.command("embed")
def embed_archive(regen: bool = False) -> None:
    """Generate embeddings for archived tweets. Resumes by default."""
    from tqdm import tqdm

    from tweetxvault.embed import EmbeddingEngine

    console = _configure_logging()
    store, _ = _open_store_for_read(console)
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
                texts = [f"@{row['author_username'] or ''}: {row['text'] or ''}" for row in batch]
                vectors = engine.embed_batch(texts)
                store.write_embeddings(batch, vectors)
                pbar.update(len(batch))
        console.print("compacting archive...")
        store.optimize()
        console.print(f"embedded {remaining} tweets")
    finally:
        store.close()


@app.command("search")
def search_archive(
    query: str,
    limit: int = 20,
    mode: str = "auto",
) -> None:
    """Search archived tweets. Modes: auto, fts, vector, hybrid."""
    console = _configure_logging()
    store, _ = _open_store_for_read(console)
    try:
        has_vec = store.has_embeddings()
        if mode == "auto":
            mode = "hybrid" if has_vec else "fts"
        if mode in ("vector", "hybrid") and not has_vec:
            console.print("[yellow]No embeddings found. Run 'tweetxvault embed' first.[/yellow]")
            console.print("Falling back to full-text search.")
            mode = "fts"

        if mode == "fts":
            results = _with_auto_optimize(
                store, console, lambda s: s.search_fts(query, limit=limit)
            )
        elif mode == "vector":
            from tweetxvault.embed import EmbeddingEngine

            engine = EmbeddingEngine()
            vec = engine.embed_batch([query])[0].tolist()
            results = store.search_vector(vec, limit=limit)
        else:
            from tweetxvault.embed import EmbeddingEngine

            engine = EmbeddingEngine()
            vec = engine.embed_batch([query])[0].tolist()
            results = _with_auto_optimize(
                store, console, lambda s: s.search_hybrid(query, vec, limit=limit)
            )

        if not results:
            console.print("[yellow]No results found.[/yellow]")
            return

        table = Table(title=f"search: {query}")
        table.add_column("Score", style="yellow", no_wrap=True)
        table.add_column("Created", style="cyan", no_wrap=True)
        table.add_column("Author", style="green", no_wrap=True)
        table.add_column("Text", overflow="fold")
        for row in results:
            score = row.get("_relevance_score") or row.get("_distance") or ""
            if isinstance(score, float):
                score = f"{score:.3f}"
            username = row.get("author_username") or row.get("author_id") or "?"
            text = (row.get("text") or "").replace("\n", " ")
            table.add_row(
                str(score),
                row.get("created_at") or "",
                f"@{username}",
                text,
            )
        console.print(table)
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
