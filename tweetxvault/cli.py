"""Typer CLI entrypoint."""

from __future__ import annotations

import asyncio
import os
import resource
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from loguru import logger
from rich import box
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from tweetxvault.articles import refresh_articles
from tweetxvault.auth import (
    BrowserCandidate,
    list_available_browser_candidates,
    resolve_auth_bundle,
)
from tweetxvault.config import ensure_paths, load_config
from tweetxvault.exceptions import ConfigError, TweetXVaultError
from tweetxvault.export import export_html_archive, export_json_archive
from tweetxvault.export.common import (
    default_export_path,
    display_collection_name,
    normalize_collection_name,
    tweet_url,
)
from tweetxvault.media import download_media
from tweetxvault.query_ids import QueryIdStore, refresh_query_ids
from tweetxvault.storage import open_archive_store
from tweetxvault.sync import run_preflight, sync_all, sync_collection
from tweetxvault.unfurl import unfurl_urls

app = typer.Typer(no_args_is_help=True)
article_app = typer.Typer(no_args_is_help=True)
auth_app = typer.Typer(no_args_is_help=True)
export_app = typer.Typer(no_args_is_help=True)
media_app = typer.Typer(no_args_is_help=True)
sync_app = typer.Typer(no_args_is_help=True)
view_app = typer.Typer(no_args_is_help=True)

app.add_typer(article_app, name="articles")
app.add_typer(auth_app, name="auth")
app.add_typer(export_app, name="export")
app.add_typer(media_app, name="media")
app.add_typer(sync_app, name="sync")
app.add_typer(view_app, name="view")


BROWSER_HELP = (
    "Browser to use for cookie extraction: firefox, chrome, chromium, brave, edge, "
    "opera, opera-gx, vivaldi, arc."
)
ARTICLE_BACKFILL_HELP = (
    "Rewalk existing timeline pages without resetting sync state so older items can pick up "
    "new article fields."
)


def _configure_logging() -> Console:
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    return Console(stderr=True)


def _browser_only_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in ("TWEETXVAULT_AUTH_TOKEN", "TWEETXVAULT_CT0", "TWEETXVAULT_USER_ID"):
        env.pop(key, None)
    return env


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
    interactive: bool = False,
):
    if interactive and (profile or profile_path is not None):
        raise ConfigError("--interactive cannot be combined with --profile or --profile-path.")
    if interactive:
        candidate = _pick_browser_candidate_interactively(console, browser=browser)
        browser = candidate.browser_id
        profile = None
        profile_path = candidate.profile_path

    if (profile or profile_path is not None) and not browser:
        raise ConfigError("--profile and --profile-path require --browser.")
    if not browser:
        return config, None

    auth = config.auth.model_copy(
        update={
            "auth_token": None,
            "ct0": None,
            "user_id": None,
            "browser": browser,
            "browser_profile": profile,
            "browser_profile_path": str(profile_path) if profile_path is not None else None,
            "firefox_profile_path": None,
        }
    )
    forced_config = config.model_copy(update={"auth": auth})
    auth_bundle = resolve_auth_bundle(forced_config, env=_browser_only_env())
    return forced_config, auth_bundle


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
        raw_text = (row["text"] or "").replace("\n", " ")
        text = raw_text[:280] + "…" if len(raw_text) > 280 else raw_text
        table.add_row(
            _format_created_at(row["created_at"]),
            f"@{username}",
            text,
            tweet_url(row),
        )

    console.print(f"showing {len(shown)} of {len(rows)} archived {label} tweets")
    console.print(table)


@sync_app.command("bookmarks")
def sync_bookmarks(
    full: bool = False,
    backfill: bool = False,
    article_backfill: Annotated[
        bool,
        typer.Option("--article-backfill", help=ARTICLE_BACKFILL_HELP),
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
        config, _ = load_config()
        config, auth_bundle = _prepare_auth_override(
            config,
            console,
            browser=browser,
            profile=profile,
            profile_path=profile_path,
        )
        result = asyncio.run(
            sync_collection(
                "bookmarks",
                full=full,
                backfill=backfill,
                article_backfill=article_backfill,
                limit=limit,
                config=config,
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
        "bookmarks: "
        f"{result.pages_fetched} pages, "
        f"{result.tweets_seen} tweets, "
        f"{result.stop_reason}"
    )


@sync_app.command("likes")
def sync_likes(
    full: bool = False,
    backfill: bool = False,
    article_backfill: Annotated[
        bool,
        typer.Option("--article-backfill", help=ARTICLE_BACKFILL_HELP),
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
        config, _ = load_config()
        config, auth_bundle = _prepare_auth_override(
            config,
            console,
            browser=browser,
            profile=profile,
            profile_path=profile_path,
        )
        result = asyncio.run(
            sync_collection(
                "likes",
                full=full,
                backfill=backfill,
                article_backfill=article_backfill,
                limit=limit,
                config=config,
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
        f"likes: {result.pages_fetched} pages, {result.tweets_seen} tweets, {result.stop_reason}"
    )


@sync_app.command("all")
def sync_everything(
    full: bool = False,
    backfill: bool = False,
    article_backfill: Annotated[
        bool,
        typer.Option("--article-backfill", help=ARTICLE_BACKFILL_HELP),
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
        config, _ = load_config()
        config, auth_bundle = _prepare_auth_override(
            config,
            console,
            browser=browser,
            profile=profile,
            profile_path=profile_path,
        )
        outcome = asyncio.run(
            sync_all(
                full=full,
                backfill=backfill,
                article_backfill=article_backfill,
                limit=limit,
                config=config,
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
            interactive=interactive,
        )
        result = asyncio.run(
            run_preflight(
                config=config,
                paths=paths,
                collections=["bookmarks", "likes"],
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
    """Rebuild normalized tweet fields and secondary rows from stored raw_json."""
    from tqdm import tqdm

    console = _configure_logging()
    store, _ = _open_store_for_read(console)
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
