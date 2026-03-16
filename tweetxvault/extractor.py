"""Normalize tweet payloads into tweet/media/url/article objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "ref_src",
    "ref_url",
}


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value:
            continue
        return value
    return None


def _deep_first_string(
    node: Any,
    keys: tuple[str, ...],
    *,
    absolute_url_only: bool = False,
) -> str | None:
    if isinstance(node, dict):
        for key in keys:
            value = node.get(key)
            if isinstance(value, str) and value:
                if absolute_url_only and not value.startswith(("http://", "https://")):
                    continue
                return value
        for value in node.values():
            found = _deep_first_string(value, keys, absolute_url_only=absolute_url_only)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _deep_first_string(item, keys, absolute_url_only=absolute_url_only)
            if found:
                return found
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def unwrap_tweet_result(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    typename = result.get("__typename")
    if typename == "TweetWithVisibilityResults":
        return unwrap_tweet_result(result.get("tweet"))
    if typename in {"TweetTombstone", "TweetUnavailable"}:
        return None
    if result.get("rest_id"):
        return result
    return None


def extract_author_fields(tweet: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    core = tweet.get("core") or {}
    user_result = core.get("user_results", {}).get("result") or {}
    if not isinstance(user_result, dict):
        user_result = {}
    user_legacy = user_result.get("legacy") or {}
    user_core = user_result.get("core") or {}
    return (
        user_result.get("rest_id"),
        user_legacy.get("screen_name") or user_core.get("screen_name"),
        user_legacy.get("name") or user_core.get("name"),
    )


def extract_note_tweet_text(tweet: dict[str, Any]) -> str | None:
    note_result = (tweet.get("note_tweet") or {}).get("note_tweet_results", {}).get("result") or {}
    if not isinstance(note_result, dict):
        return None
    return note_result.get("text") or (note_result.get("richtext") or {}).get("text")


def extract_canonical_text(tweet: dict[str, Any]) -> str:
    return extract_note_tweet_text(tweet) or (tweet.get("legacy") or {}).get("full_text", "")


def _iso8601_from_unix(value: Any) -> str | None:
    seconds = _as_int(value)
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat()


def canonicalize_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower() if parsed.hostname else ""
    if not hostname:
        return None
    if parsed.port and not (
        (scheme == "http" and parsed.port == 80) or (scheme == "https" and parsed.port == 443)
    ):
        netloc = f"{hostname}:{parsed.port}"
    else:
        netloc = hostname
    query_pairs = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in _TRACKING_QUERY_KEYS:
            continue
        query_pairs.append((key, item))
    path = parsed.path or "/"
    query = urlencode(query_pairs, doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def extract_status_id_from_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    hostname = parsed.hostname.lower() if parsed.hostname else ""
    if not (
        hostname == "x.com"
        or hostname.endswith(".x.com")
        or hostname == "twitter.com"
        or hostname.endswith(".twitter.com")
    ):
        return None
    parts = [part for part in parsed.path.split("/") if part]
    for index, part in enumerate(parts):
        if part == "status" and index + 1 < len(parts):
            candidate = parts[index + 1]
            if candidate.isdigit():
                return candidate
    return None


def _article_result(tweet: dict[str, Any]) -> dict[str, Any] | None:
    article_result = (tweet.get("article") or {}).get("article_results", {}).get("result") or (
        tweet.get("article_results") or {}
    ).get("result")
    return article_result if isinstance(article_result, dict) else None


@dataclass(slots=True)
class TweetObjectData:
    tweet_id: str
    text: str
    author_id: str | None
    author_username: str | None
    author_display_name: str | None
    created_at: str | None
    conversation_id: str | None
    lang: str | None
    note_tweet_text: str | None
    raw_json: dict[str, Any]


@dataclass(slots=True)
class TweetRelationData:
    source_tweet_id: str
    relation_type: str
    target_tweet_id: str
    raw_json: dict[str, Any]


@dataclass(slots=True)
class MediaData:
    tweet_id: str
    position: int
    media_key: str
    media_type: str | None
    media_url: str | None
    thumbnail_url: str | None
    width: int | None
    height: int | None
    duration_millis: int | None
    variants: list[dict[str, Any]]
    raw_json: dict[str, Any]
    source: str | None = None
    article_id: str | None = None


@dataclass(slots=True)
class UrlData:
    url_hash: str
    canonical_url: str
    expanded_url: str | None
    host: str | None
    raw_json: dict[str, Any]
    final_url: str | None = None
    title: str | None = None
    description: str | None = None
    site_name: str | None = None


@dataclass(slots=True)
class UrlRefData:
    tweet_id: str
    position: int
    short_url: str | None
    expanded_url: str | None
    canonical_url: str | None
    display_url: str | None
    url_hash: str | None
    raw_json: dict[str, Any]


@dataclass(slots=True)
class ArticleData:
    tweet_id: str
    article_id: str
    title: str | None
    summary_text: str | None
    content_text: str | None
    canonical_url: str | None
    published_at: str | None
    status: str
    raw_json: dict[str, Any]


@dataclass(slots=True)
class ExtractedTweetGraph:
    tweet_objects: dict[str, TweetObjectData] = field(default_factory=dict)
    relations: dict[tuple[str, str, str], TweetRelationData] = field(default_factory=dict)
    media: dict[tuple[str, str], MediaData] = field(default_factory=dict)
    urls: dict[str, UrlData] = field(default_factory=dict)
    url_refs: dict[tuple[str, int], UrlRefData] = field(default_factory=dict)
    articles: dict[str, ArticleData] = field(default_factory=dict)

    def add_tweet_object(self, item: TweetObjectData) -> None:
        existing = self.tweet_objects.get(item.tweet_id)
        if existing is None:
            self.tweet_objects[item.tweet_id] = item
            return
        self.tweet_objects[item.tweet_id] = TweetObjectData(
            tweet_id=item.tweet_id,
            text=_coalesce(item.text, existing.text) or "",
            author_id=_coalesce(item.author_id, existing.author_id),
            author_username=_coalesce(item.author_username, existing.author_username),
            author_display_name=_coalesce(item.author_display_name, existing.author_display_name),
            created_at=_coalesce(item.created_at, existing.created_at),
            conversation_id=_coalesce(item.conversation_id, existing.conversation_id),
            lang=_coalesce(item.lang, existing.lang),
            note_tweet_text=_coalesce(item.note_tweet_text, existing.note_tweet_text),
            raw_json=item.raw_json or existing.raw_json,
        )

    def add_relation(self, item: TweetRelationData) -> None:
        self.relations[(item.source_tweet_id, item.relation_type, item.target_tweet_id)] = item

    def add_media(self, item: MediaData) -> None:
        key = (item.tweet_id, item.media_key)
        existing = self.media.get(key)
        if existing is None:
            self.media[key] = item
            return
        self.media[key] = MediaData(
            tweet_id=item.tweet_id,
            position=min(existing.position, item.position),
            media_key=item.media_key,
            media_type=_coalesce(item.media_type, existing.media_type),
            media_url=_coalesce(item.media_url, existing.media_url),
            thumbnail_url=_coalesce(item.thumbnail_url, existing.thumbnail_url),
            width=_coalesce(item.width, existing.width),
            height=_coalesce(item.height, existing.height),
            duration_millis=_coalesce(item.duration_millis, existing.duration_millis),
            variants=item.variants or existing.variants,
            raw_json=item.raw_json or existing.raw_json,
            source=_coalesce(item.source, existing.source),
            article_id=_coalesce(item.article_id, existing.article_id),
        )

    def add_url(self, item: UrlData) -> None:
        existing = self.urls.get(item.url_hash)
        if existing is None:
            self.urls[item.url_hash] = item
            return
        self.urls[item.url_hash] = UrlData(
            url_hash=item.url_hash,
            canonical_url=item.canonical_url,
            expanded_url=_coalesce(item.expanded_url, existing.expanded_url),
            host=_coalesce(item.host, existing.host),
            raw_json=item.raw_json or existing.raw_json,
            final_url=_coalesce(item.final_url, existing.final_url),
            title=_coalesce(item.title, existing.title),
            description=_coalesce(item.description, existing.description),
            site_name=_coalesce(item.site_name, existing.site_name),
        )

    def add_url_ref(self, item: UrlRefData) -> None:
        key = (item.tweet_id, item.position)
        existing = self.url_refs.get(key)
        if existing is None:
            self.url_refs[key] = item
            return
        self.url_refs[key] = UrlRefData(
            tweet_id=item.tweet_id,
            position=item.position,
            short_url=_coalesce(item.short_url, existing.short_url),
            expanded_url=_coalesce(item.expanded_url, existing.expanded_url),
            canonical_url=_coalesce(item.canonical_url, existing.canonical_url),
            display_url=_coalesce(item.display_url, existing.display_url),
            url_hash=_coalesce(item.url_hash, existing.url_hash),
            raw_json=item.raw_json or existing.raw_json,
        )

    def add_article(self, item: ArticleData) -> None:
        existing = self.articles.get(item.tweet_id)
        if existing is None:
            self.articles[item.tweet_id] = item
            return
        content_text = _coalesce(item.content_text, existing.content_text)
        self.articles[item.tweet_id] = ArticleData(
            tweet_id=item.tweet_id,
            article_id=_coalesce(item.article_id, existing.article_id) or item.tweet_id,
            title=_coalesce(item.title, existing.title),
            summary_text=_coalesce(item.summary_text, existing.summary_text),
            content_text=content_text,
            canonical_url=_coalesce(item.canonical_url, existing.canonical_url),
            published_at=_coalesce(item.published_at, existing.published_at),
            status="body_present" if content_text else "preview_only",
            raw_json=item.raw_json or existing.raw_json,
        )

    def merge(self, other: ExtractedTweetGraph) -> None:
        for item in other.tweet_objects.values():
            self.add_tweet_object(item)
        for item in other.relations.values():
            self.add_relation(item)
        for item in other.media.values():
            self.add_media(item)
        for item in other.urls.values():
            self.add_url(item)
        for item in other.url_refs.values():
            self.add_url_ref(item)
        for item in other.articles.values():
            self.add_article(item)


def _tweet_object(tweet: dict[str, Any]) -> TweetObjectData | None:
    tweet_id = tweet.get("rest_id")
    if not tweet_id:
        return None
    author_id, author_username, author_display_name = extract_author_fields(tweet)
    legacy = tweet.get("legacy") or {}
    return TweetObjectData(
        tweet_id=tweet_id,
        text=extract_canonical_text(tweet),
        author_id=author_id,
        author_username=author_username,
        author_display_name=author_display_name,
        created_at=legacy.get("created_at"),
        conversation_id=legacy.get("conversation_id_str"),
        lang=legacy.get("lang"),
        note_tweet_text=extract_note_tweet_text(tweet),
        raw_json=tweet,
    )


def _tweet_urls(tweet: dict[str, Any]) -> list[dict[str, Any]]:
    urls: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None, str | None]] = set()
    note_result = (tweet.get("note_tweet") or {}).get("note_tweet_results", {}).get("result") or {}
    if isinstance(note_result, dict):
        note_urls = (note_result.get("entity_set") or {}).get("urls")
        if isinstance(note_urls, list):
            for item in note_urls:
                if not isinstance(item, dict):
                    continue
                identity = (
                    item.get("url") if isinstance(item.get("url"), str) else None,
                    item.get("expanded_url") if isinstance(item.get("expanded_url"), str) else None,
                    item.get("display_url") if isinstance(item.get("display_url"), str) else None,
                )
                if identity in seen:
                    continue
                seen.add(identity)
                urls.append(item)
    legacy_urls = ((tweet.get("legacy") or {}).get("entities") or {}).get("urls")
    if isinstance(legacy_urls, list):
        for item in legacy_urls:
            if not isinstance(item, dict):
                continue
            identity = (
                item.get("url") if isinstance(item.get("url"), str) else None,
                item.get("expanded_url") if isinstance(item.get("expanded_url"), str) else None,
                item.get("display_url") if isinstance(item.get("display_url"), str) else None,
            )
            if identity in seen:
                continue
            seen.add(identity)
            urls.append(item)
    return urls


def _url_candidate(
    url_item: dict[str, Any],
    *,
    keys: tuple[str, ...],
    require_absolute: bool = False,
) -> str | None:
    for key in keys:
        value = url_item.get(key)
        found = _deep_first_string(
            value,
            ("url", "expanded_url", "string_value"),
            absolute_url_only=require_absolute,
        )
        if found:
            return found
        if isinstance(value, str) and value:
            if require_absolute and not value.startswith(("http://", "https://")):
                continue
            return value
    return None


def _payload_url_metadata(url_item: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    unwound = url_item.get("unwound_url")
    if not isinstance(unwound, dict):
        return None, None, None
    title = _deep_first_string(unwound, ("title", "title_text", "page_title"))
    description = _deep_first_string(
        unwound,
        ("description", "description_text", "full_text", "subtitle"),
    )
    site_name = _deep_first_string(unwound, ("site_name", "site", "publisher"))
    return title, description, site_name


def _url_entries(tweet: dict[str, Any]) -> tuple[list[UrlRefData], list[UrlData]]:
    tweet_id = tweet.get("rest_id")
    if not tweet_id:
        return [], []
    refs: list[UrlRefData] = []
    urls: list[UrlData] = []
    for position, item in enumerate(_tweet_urls(tweet)):
        short_url = item.get("url")
        expanded_url = item.get("expanded_url")
        final_url = _url_candidate(
            item,
            keys=("unwound_url", "expanded_url"),
            require_absolute=True,
        )
        canonical_url = canonicalize_url(
            _url_candidate(item, keys=("unwound_url", "expanded_url", "url"))
        )
        url_hash = sha256(canonical_url.encode("utf-8")).hexdigest() if canonical_url else None
        refs.append(
            UrlRefData(
                tweet_id=tweet_id,
                position=position,
                short_url=short_url if isinstance(short_url, str) else None,
                expanded_url=expanded_url if isinstance(expanded_url, str) else None,
                canonical_url=canonical_url,
                display_url=item.get("display_url")
                if isinstance(item.get("display_url"), str)
                else None,
                url_hash=url_hash,
                raw_json=item,
            )
        )
        if canonical_url:
            title, description, site_name = _payload_url_metadata(item)
            parsed = urlsplit(canonical_url)
            urls.append(
                UrlData(
                    url_hash=url_hash or sha256(canonical_url.encode("utf-8")).hexdigest(),
                    canonical_url=canonical_url,
                    expanded_url=expanded_url if isinstance(expanded_url, str) else canonical_url,
                    host=parsed.hostname,
                    raw_json=item,
                    final_url=final_url if isinstance(final_url, str) else None,
                    title=title,
                    description=description,
                    site_name=site_name,
                )
            )
    return refs, urls


def _media_dimensions(item: dict[str, Any]) -> tuple[int | None, int | None]:
    original = item.get("original_info") or {}
    width = _as_int(original.get("width"))
    height = _as_int(original.get("height"))
    if width is not None or height is not None:
        return width, height
    sizes = item.get("sizes") or {}
    for name in ("large", "medium", "small", "thumb"):
        size = sizes.get(name) or {}
        width = _as_int(size.get("w"))
        height = _as_int(size.get("h"))
        if width is not None or height is not None:
            return width, height
    return None, None


def _video_variants(item: dict[str, Any]) -> list[dict[str, Any]]:
    variants = ((item.get("video_info") or {}).get("variants")) or []
    cleaned: list[dict[str, Any]] = []
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        url = variant.get("url")
        if not isinstance(url, str) or not url:
            continue
        cleaned.append(
            {
                "bitrate": _as_int(variant.get("bitrate")),
                "content_type": variant.get("content_type"),
                "url": url,
            }
        )
    return cleaned


def _preferred_media_url(item: dict[str, Any], variants: list[dict[str, Any]]) -> str | None:
    media_url = item.get("media_url_https") or item.get("media_url")
    if isinstance(media_url, str) and media_url and item.get("type") == "photo":
        return media_url
    mp4_variants = [
        variant
        for variant in variants
        if variant.get("content_type") == "video/mp4" and variant.get("url")
    ]
    if mp4_variants:
        best = max(mp4_variants, key=lambda variant: variant.get("bitrate") or -1)
        return best["url"]
    if variants:
        return variants[0]["url"]
    return media_url if isinstance(media_url, str) else None


def _media_entries(tweet: dict[str, Any]) -> list[MediaData]:
    tweet_id = tweet.get("rest_id")
    if not tweet_id:
        return []
    legacy = tweet.get("legacy") or {}
    entities = legacy.get("extended_entities") or legacy.get("entities") or {}
    media_items = entities.get("media")
    if not isinstance(media_items, list):
        return []
    records: list[MediaData] = []
    for position, item in enumerate(media_items):
        if not isinstance(item, dict):
            continue
        media_key = item.get("media_key") or f"idx-{position}"
        variants = _video_variants(item)
        width, height = _media_dimensions(item)
        records.append(
            MediaData(
                tweet_id=tweet_id,
                position=position,
                media_key=str(media_key),
                media_type=item.get("type") if isinstance(item.get("type"), str) else None,
                media_url=_preferred_media_url(item, variants),
                thumbnail_url=item.get("media_url_https") or item.get("media_url"),
                width=width,
                height=height,
                duration_millis=_as_int((item.get("video_info") or {}).get("duration_millis")),
                variants=variants,
                raw_json=item,
                source="tweet_media",
            )
        )
    return records


def _article_media_dimensions(item: dict[str, Any]) -> tuple[int | None, int | None]:
    media_info = item.get("media_info") or {}
    return (
        _as_int(media_info.get("original_img_width")) or _as_int(media_info.get("width")),
        _as_int(media_info.get("original_img_height")) or _as_int(media_info.get("height")),
    )


def _article_media_entries(
    tweet: dict[str, Any],
    article_result: dict[str, Any],
    *,
    article_id: str,
) -> list[MediaData]:
    tweet_id = tweet.get("rest_id")
    if not tweet_id:
        return []
    records: list[MediaData] = []
    candidates: list[tuple[str, int, dict[str, Any]]] = []
    cover_media = article_result.get("cover_media")
    if isinstance(cover_media, dict):
        candidates.append(("article_cover", 0, cover_media))
    media_entities = article_result.get("media_entities")
    if isinstance(media_entities, list):
        base = len(candidates)
        for offset, item in enumerate(media_entities):
            if isinstance(item, dict):
                candidates.append(("article_media", base + offset, item))
    for source, position, item in candidates:
        media_info = item.get("media_info") or {}
        if source == "article_cover":
            media_key = f"article-cover:{tweet_id}"
        else:
            media_key = item.get("media_key") or f"{source}:{tweet_id}:{position}"
        width, height = _article_media_dimensions(item)
        media_url = _deep_first_string(
            media_info,
            ("original_img_url", "media_url_https", "media_url", "url"),
            absolute_url_only=True,
        )
        records.append(
            MediaData(
                tweet_id=tweet_id,
                position=position,
                media_key=str(media_key),
                media_type="photo",
                media_url=media_url,
                thumbnail_url=media_url,
                width=width,
                height=height,
                duration_millis=None,
                variants=[],
                raw_json=item,
                source=source,
                article_id=article_id,
            )
        )
    return records


def _article_entry(
    tweet: dict[str, Any], article_result: dict[str, Any] | None = None
) -> ArticleData | None:
    tweet_id = tweet.get("rest_id")
    if not tweet_id:
        return None
    article_result = article_result or _article_result(tweet)
    if article_result is None:
        return None
    article_id = article_result.get("rest_id") or article_result.get("id") or tweet_id
    title = article_result.get("title")
    summary_text = _coalesce(
        article_result.get("summary_text"),
        article_result.get("preview_text"),
        _deep_first_string(article_result, ("summary_text", "preview_text")),
    )
    content_text = _coalesce(
        article_result.get("plain_text"),
        article_result.get("content_text"),
        article_result.get("body_text"),
        _deep_first_string(
            article_result,
            (
                "plain_text",
                "article_plain_text",
                "content_text",
                "body_text",
                "text",
            ),
        ),
    )
    article_url = canonicalize_url(
        _coalesce(
            article_result.get("url"),
            _deep_first_string(
                article_result,
                ("permalink", "article_url", "url"),
                absolute_url_only=True,
            ),
        )
    )
    return ArticleData(
        tweet_id=tweet_id,
        article_id=str(article_id),
        title=title if isinstance(title, str) else None,
        summary_text=summary_text if isinstance(summary_text, str) else None,
        content_text=content_text if isinstance(content_text, str) else None,
        canonical_url=article_url,
        published_at=_iso8601_from_unix(
            (article_result.get("metadata") or {}).get("first_published_at_secs")
        ),
        status="body_present" if content_text else "preview_only",
        raw_json=article_result,
    )


def _attached_tweets(tweet: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    legacy = tweet.get("legacy") or {}
    pairs = [
        (
            "retweet_of",
            unwrap_tweet_result((legacy.get("retweeted_status_result") or {}).get("result")),
        ),
        ("quote_of", unwrap_tweet_result((tweet.get("quoted_status_result") or {}).get("result"))),
    ]
    return [(relation_type, item) for relation_type, item in pairs if item is not None]


def _visit_tweet(
    tweet: dict[str, Any], graph: ExtractedTweetGraph, *, expand_attached: bool
) -> None:
    tweet_object = _tweet_object(tweet)
    if tweet_object is None:
        return
    graph.add_tweet_object(tweet_object)
    for item in _media_entries(tweet):
        graph.add_media(item)
    url_refs, urls = _url_entries(tweet)
    for item in url_refs:
        graph.add_url_ref(item)
        linked_status_id = _coalesce(
            extract_status_id_from_url(item.canonical_url),
            extract_status_id_from_url(item.expanded_url),
            extract_status_id_from_url(item.short_url),
        )
        if linked_status_id and linked_status_id != tweet_object.tweet_id:
            graph.add_relation(
                TweetRelationData(
                    source_tweet_id=tweet_object.tweet_id,
                    relation_type="links_to_status",
                    target_tweet_id=linked_status_id,
                    raw_json={
                        "relation_type": "links_to_status",
                        "target_tweet_id": linked_status_id,
                    },
                )
            )
    for item in urls:
        graph.add_url(item)
    article_result = _article_result(tweet)
    article = _article_entry(tweet, article_result)
    if article:
        graph.add_article(article)
        if article_result is not None:
            for item in _article_media_entries(
                tweet, article_result, article_id=article.article_id
            ):
                graph.add_media(item)
    if not expand_attached:
        return
    for relation_type, attached in _attached_tweets(tweet):
        target_id = attached.get("rest_id")
        if not target_id:
            continue
        graph.add_relation(
            TweetRelationData(
                source_tweet_id=tweet_object.tweet_id,
                relation_type=relation_type,
                target_tweet_id=target_id,
                raw_json={"relation_type": relation_type, "target_tweet_id": target_id},
            )
        )
        _visit_tweet(attached, graph, expand_attached=False)


def extract_secondary_objects(root_tweet: dict[str, Any]) -> ExtractedTweetGraph:
    return extract_secondary_objects_from_tweets([root_tweet])


def extract_secondary_objects_from_tweets(root_tweets: list[dict[str, Any]]) -> ExtractedTweetGraph:
    graph = ExtractedTweetGraph()
    for root_tweet in root_tweets:
        tweet = unwrap_tweet_result(root_tweet) or root_tweet
        if isinstance(tweet, dict):
            _visit_tweet(tweet, graph, expand_attached=True)
    return graph


def extract_thread_objects(root_tweets: list[dict[str, Any]]) -> ExtractedTweetGraph:
    graph = extract_secondary_objects_from_tweets(root_tweets)
    known_ids: set[str] = set()
    for root_tweet in root_tweets:
        tweet = unwrap_tweet_result(root_tweet) or root_tweet
        if not isinstance(tweet, dict):
            continue
        tweet_id = tweet.get("rest_id")
        if isinstance(tweet_id, str) and tweet_id:
            known_ids.add(tweet_id)
    for root_tweet in root_tweets:
        tweet = unwrap_tweet_result(root_tweet) or root_tweet
        if not isinstance(tweet, dict):
            continue
        tweet_id = tweet.get("rest_id")
        legacy = tweet.get("legacy") or {}
        parent_id = legacy.get("in_reply_to_status_id_str")
        if not isinstance(tweet_id, str) or not tweet_id:
            continue
        if not isinstance(parent_id, str) or not parent_id or parent_id == tweet_id:
            continue
        graph.add_relation(
            TweetRelationData(
                source_tweet_id=tweet_id,
                relation_type="reply_to",
                target_tweet_id=parent_id,
                raw_json={"relation_type": "reply_to", "target_tweet_id": parent_id},
            )
        )
        if parent_id in known_ids:
            graph.add_relation(
                TweetRelationData(
                    source_tweet_id=tweet_id,
                    relation_type="thread_parent",
                    target_tweet_id=parent_id,
                    raw_json={"relation_type": "thread_parent", "target_tweet_id": parent_id},
                )
            )
            graph.add_relation(
                TweetRelationData(
                    source_tweet_id=parent_id,
                    relation_type="thread_child",
                    target_tweet_id=tweet_id,
                    raw_json={"relation_type": "thread_child", "target_tweet_id": tweet_id},
                )
            )
    return graph
