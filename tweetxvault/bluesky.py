"""Public Bluesky likes collection and normalization."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit

import httpx
from rich.console import Console

from tweetxvault.client.timelines import TimelineTweet
from tweetxvault.config import AppConfig, XDGPaths
from tweetxvault.extractor import (
    ExtractedTweetGraph,
    MediaData,
    TweetObjectData,
    UrlData,
    UrlRefData,
    canonicalize_url,
)
from tweetxvault.storage import open_archive_store
from tweetxvault.storage.backend import BLUESKY_SOURCE
from tweetxvault.sync import ProcessLock

PUBLIC_APPVIEW = "https://public.api.bsky.app/xrpc"
LIKE_COLLECTION = "app.bsky.feed.like"


@dataclass(slots=True)
class BlueskySyncResult:
    handle: str
    pages_fetched: int
    likes_seen: int
    posts_archived: int
    posts_hydrated: int
    stop_reason: str


def _post_id(uri: str) -> str:
    try:
        did, collection, rkey = uri.removeprefix("at://").split("/", 2)
    except ValueError as exc:
        raise ValueError(f"Invalid AT URI: {uri}") from exc
    if collection != "app.bsky.feed.post":
        raise ValueError(f"Expected a Bluesky post URI, got: {uri}")
    return f"bsky:{did}:{rkey}"


def _post_url(uri: str, handle: str) -> str:
    rkey = uri.rsplit("/", 1)[-1]
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


async def _resolve_identity(client: httpx.AsyncClient, handle: str) -> tuple[str, str]:
    response = await client.get(
        f"{PUBLIC_APPVIEW}/com.atproto.identity.resolveHandle", params={"handle": handle}
    )
    response.raise_for_status()
    did = response.json()["did"]
    if did.startswith("did:plc:"):
        did_url = f"https://plc.directory/{did}"
    elif did.startswith("did:web:"):
        host = did.removeprefix("did:web:").replace(":", "/")
        did_url = f"https://{host}/.well-known/did.json"
    else:
        raise ValueError(f"Unsupported Bluesky DID method: {did}")
    response = await client.get(did_url)
    response.raise_for_status()
    services = response.json().get("service") or []
    pds = next(
        (
            item.get("serviceEndpoint")
            for item in services
            if item.get("id") == "#atproto_pds" or item.get("type") == "AtprotoPersonalDataServer"
        ),
        None,
    )
    if not isinstance(pds, str) or not pds.startswith("https://"):
        raise ValueError(f"Could not resolve a secure PDS for {handle}")
    return did, pds.rstrip("/")


def _add_url(
    graph: ExtractedTweetGraph,
    post_id: str,
    position: int,
    value: str,
    *,
    raw: dict[str, Any],
    title: str | None = None,
    description: str | None = None,
) -> None:
    canonical = canonicalize_url(value)
    if not canonical:
        return
    digest = sha256(canonical.encode()).hexdigest()
    graph.add_url(
        UrlData(
            url_hash=digest,
            canonical_url=canonical,
            expanded_url=value,
            final_url=value,
            host=urlsplit(canonical).hostname,
            title=title,
            description=description,
            site_name=None,
            raw_json=raw,
        )
    )
    graph.add_url_ref(
        UrlRefData(
            tweet_id=post_id,
            position=position,
            short_url=None,
            expanded_url=value,
            canonical_url=canonical,
            display_url=urlsplit(value).netloc,
            url_hash=digest,
            raw_json=raw,
        )
    )


def _normalize_post(
    view: dict[str, Any], liked_at: str
) -> tuple[TimelineTweet, ExtractedTweetGraph]:
    uri = view["uri"]
    post_id = _post_id(uri)
    author = view.get("author") or {}
    record = view.get("record") or {}
    text = record.get("text") or ""
    created_at = record.get("createdAt") or view.get("indexedAt")
    handle = author.get("handle") or author.get("did") or "profile"
    raw = {
        "source": BLUESKY_SOURCE,
        "post": view,
        "liked_at": liked_at,
        "original_url": _post_url(uri, handle),
    }
    tweet = TimelineTweet(
        tweet_id=post_id,
        text=text,
        author_id=author.get("did"),
        author_username=author.get("handle"),
        author_display_name=author.get("displayName"),
        created_at=created_at,
        sort_index=liked_at,
        raw_json=raw,
    )
    graph = ExtractedTweetGraph()
    graph.add_tweet_object(
        TweetObjectData(
            tweet_id=post_id,
            text=text,
            author_id=author.get("did"),
            author_username=author.get("handle"),
            author_display_name=author.get("displayName"),
            created_at=created_at,
            conversation_id=None,
            lang=record.get("langs", [None])[0] if record.get("langs") else None,
            note_tweet_text=None,
            raw_json=raw,
        )
    )
    _add_url(graph, post_id, 0, raw["original_url"], raw=raw)
    _normalize_embed(graph, post_id, view.get("embed") or {}, start_position=0)
    return tweet, graph


def _normalize_embed(
    graph: ExtractedTweetGraph,
    post_id: str,
    embed: dict[str, Any],
    *,
    start_position: int,
) -> None:
    kind = str(embed.get("$type") or "")
    if kind.endswith("recordWithMedia#view"):
        _normalize_embed(graph, post_id, embed.get("media") or {}, start_position=start_position)
        return
    if kind.endswith("images#view"):
        for offset, image in enumerate(embed.get("images") or []):
            ratio = image.get("aspectRatio") or {}
            graph.add_media(
                MediaData(
                    tweet_id=post_id,
                    position=start_position + offset,
                    media_key=f"bsky-image-{offset}",
                    media_type="photo",
                    media_url=image.get("fullsize"),
                    thumbnail_url=image.get("thumb"),
                    width=ratio.get("width"),
                    height=ratio.get("height"),
                    duration_millis=None,
                    variants=[],
                    raw_json=image,
                    source="bluesky",
                )
            )
    elif kind.endswith("video#view"):
        ratio = embed.get("aspectRatio") or {}
        graph.add_media(
            MediaData(
                tweet_id=post_id,
                position=start_position,
                media_key="bsky-video-0",
                media_type="video",
                media_url=embed.get("playlist"),
                thumbnail_url=embed.get("thumbnail"),
                width=ratio.get("width"),
                height=ratio.get("height"),
                duration_millis=None,
                variants=[],
                raw_json=embed,
                source="bluesky",
            )
        )
    elif kind.endswith("external#view"):
        external = embed.get("external") or {}
        if uri := external.get("uri"):
            _add_url(
                graph,
                post_id,
                1,
                uri,
                raw=external,
                title=external.get("title"),
                description=external.get("description"),
            )


async def sync_bluesky_likes(
    handle: str,
    *,
    full: bool,
    limit: int | None,
    config: AppConfig,
    paths: XDGPaths,
    console: Console,
    transport: httpx.AsyncBaseTransport | None = None,
) -> BlueskySyncResult:
    handle = handle.strip().lstrip("@").casefold()
    async with httpx.AsyncClient(
        timeout=config.sync.timeout,
        follow_redirects=True,
        transport=transport,
    ) as client:
        did, pds = await _resolve_identity(client, handle)
        lock = ProcessLock(paths.lock_file)
        lock.acquire()
        try:
            store = open_archive_store(paths, create=True)
            assert store is not None
            folder_id = f"bluesky:{did}"
            if full:
                store.reset_sync_state("like", folder_id=folder_id)
            previous_state = store.get_sync_state("like", folder_id=folder_id)
            saved_backfill = (
                previous_state.backfill_cursor if previous_state.backfill_incomplete else None
            )
            resuming_backfill = False
            existing = set() if full else store.get_collection_tweet_ids("like")
            cursor: str | None = None
            pages = likes_seen = posts_archived = posts_hydrated = 0
            stop_reason = "end_of_collection"
            try:
                while True:
                    response = await client.get(
                        f"{pds}/xrpc/com.atproto.repo.listRecords",
                        params={
                            "repo": did,
                            "collection": LIKE_COLLECTION,
                            "limit": 100,
                            **({"cursor": cursor} if cursor else {}),
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                    records = payload.get("records") or []
                    subject_uris = [
                        (record.get("value") or {}).get("subject", {}).get("uri")
                        for record in records
                    ]
                    subject_uris = [uri for uri in subject_uris if isinstance(uri, str)]
                    liked_at = {
                        (record.get("value") or {}).get("subject", {}).get("uri"): (
                            record.get("value") or {}
                        ).get("createdAt")
                        or ""
                        for record in records
                    }
                    new_uris = [uri for uri in subject_uris if _post_id(uri) not in existing]
                    views: list[dict[str, Any]] = []
                    for start in range(0, len(new_uris), 25):
                        hydrated = await client.get(
                            f"{PUBLIC_APPVIEW}/app.bsky.feed.getPosts",
                            params=[("uris", uri) for uri in new_uris[start : start + 25]],
                        )
                        hydrated.raise_for_status()
                        views.extend(hydrated.json().get("posts") or [])
                    views_by_uri = {view["uri"]: view for view in views}
                    tweets: list[TimelineTweet] = []
                    graph = ExtractedTweetGraph()
                    for uri in new_uris:
                        view = views_by_uri.get(uri)
                        if view is None:
                            author_did = uri.removeprefix("at://").split("/", 1)[0]
                            view = {
                                "uri": uri,
                                "author": {"did": author_did, "handle": author_did},
                                "record": {},
                                "unavailable": True,
                            }
                        tweet, post_graph = _normalize_post(view, liked_at.get(view["uri"], ""))
                        tweets.append(tweet)
                        graph.merge(post_graph)
                    next_cursor = payload.get("cursor")
                    pages += 1
                    likes_seen += len(records)
                    posts_archived += len(tweets)
                    posts_hydrated += len(views)
                    state_cursor = (
                        saved_backfill if saved_backfill and not resuming_backfill else next_cursor
                    )
                    store.persist_external_page(
                        operation="BlueskyLikes",
                        collection_type="like",
                        folder_id=folder_id,
                        cursor_in=cursor,
                        cursor_out=next_cursor,
                        http_status=response.status_code,
                        raw_json=payload,
                        tweets=tweets,
                        graph=graph,
                        source=BLUESKY_SOURCE,
                        last_head_tweet_id=_post_id(subject_uris[0]) if subject_uris else None,
                        backfill_cursor=state_cursor,
                        backfill_incomplete=bool(state_cursor),
                    )
                    console.print(
                        f"Bluesky likes: page {pages}, {posts_archived} new posts archived"
                    )
                    if not records or not next_cursor:
                        break
                    if not full and subject_uris and not new_uris:
                        if saved_backfill and not resuming_backfill:
                            cursor = saved_backfill
                            saved_backfill = None
                            resuming_backfill = True
                            continue
                        stop_reason = "existing_posts"
                        break
                    if limit is not None and pages >= limit:
                        stop_reason = "limit"
                        break
                    cursor = next_cursor
                    await asyncio.sleep(config.sync.page_delay)
            finally:
                store.close()
        finally:
            lock.release()
    return BlueskySyncResult(handle, pages, likes_seen, posts_archived, posts_hydrated, stop_reason)
