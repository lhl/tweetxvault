"""HTML export."""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

from tweetxvault.export.common import display_collection_name, normalize_collection_name, tweet_url
from tweetxvault.storage import ArchiveStore


def _asset_href(
    *,
    asset_base_dir: Path,
    out_dir: Path,
    local_path: str | None,
    remote_url: str | None,
) -> str | None:
    if local_path:
        asset_path = (asset_base_dir / local_path).resolve()
        return os.path.relpath(asset_path, start=out_dir.resolve())
    return remote_url


def _render_media_gallery(
    media_items: list[dict[str, Any]],
    *,
    asset_base_dir: Path,
    out_dir: Path,
) -> str:
    if not media_items:
        return ""
    cards: list[str] = []
    for item in media_items:
        download = item.get("download", {})
        if not isinstance(download, dict):
            download = {}
        href = _asset_href(
            asset_base_dir=asset_base_dir,
            out_dir=out_dir,
            local_path=download.get("local_path"),
            remote_url=item.get("url"),
        )
        preview = _asset_href(
            asset_base_dir=asset_base_dir,
            out_dir=out_dir,
            local_path=download.get("thumbnail_local_path") or download.get("local_path"),
            remote_url=item.get("thumbnail_url") or item.get("url"),
        )
        media_type = str(item.get("type") or "media")
        status = str(download.get("state") or "pending")
        image = (
            f'<img src="{escape(preview)}" alt="{escape(media_type)}" loading="lazy">'
            if preview
            else '<div class="media-placeholder">media</div>'
        )
        inner = (
            f'<a class="media-thumb" href="{escape(href)}">{image}</a>'
            if href
            else f'<div class="media-thumb">{image}</div>'
        )
        cards.append(
            f"""
            <figure class="media-card">
              {inner}
              <figcaption>
                <span class="media-kind">{escape(media_type)}</span>
                <span class="media-status">{escape(status)}</span>
              </figcaption>
            </figure>
            """.strip()
        )
    return '<div class="media-grid">{}</div>'.format("\n".join(cards))


def _render_url_list(urls: list[dict[str, Any]]) -> str:
    if not urls:
        return ""
    items: list[str] = []
    for item in urls:
        resolved = item.get("resolved", {})
        if not isinstance(resolved, dict):
            resolved = {}
        href = (
            resolved.get("final_url") or resolved.get("canonical_url") or item.get("expanded_url")
        )
        title = resolved.get("title") or resolved.get("canonical_url") or item.get("display_url")
        description = resolved.get("description")
        meta = []
        if resolved.get("site_name"):
            meta.append(str(resolved["site_name"]))
        if resolved.get("unfurl_state"):
            meta.append(str(resolved["unfurl_state"]))
        items.append(
            """
            <li class="url-item">
              <a href="{href}">{title}</a>
              <p>{description}</p>
              <span>{meta}</span>
            </li>
            """.format(
                href=escape(str(href or item.get("expanded_url") or item.get("short_url") or "")),
                title=escape(str(title or item.get("short_url") or "")),
                description=escape(str(description or item.get("display_url") or "")),
                meta=escape(" · ".join(meta)),
            ).strip()
        )
    return '<ul class="url-list">{}</ul>'.format("\n".join(items))


def _render_article_section(
    article: dict[str, Any] | None,
    *,
    asset_base_dir: Path,
    out_dir: Path,
) -> str:
    if not isinstance(article, dict):
        return ""
    article_media = article.get("media", [])
    if not isinstance(article_media, list):
        article_media = []
    title = article.get("title") or "Untitled article"
    summary = article.get("summary_text") or ""
    content = article.get("content_text") or ""
    published_at = article.get("published_at") or ""
    canonical_url = article.get("canonical_url") or ""
    media_html = _render_media_gallery(
        article_media,
        asset_base_dir=asset_base_dir,
        out_dir=out_dir,
    )
    link_html = f'<a href="{escape(str(canonical_url))}">open article</a>' if canonical_url else ""
    return (
        f"""
        <section class="article-block">
          <header>
            <h2>{escape(str(title))}</h2>
            <p class="article-meta">{escape(str(published_at))} {link_html}</p>
          </header>
          <p class="article-summary">{escape(str(summary))}</p>
          {media_html}
          <div class="article-body">{escape(str(content))}</div>
        </section>
        """
    ).strip()


def _render_html_archive(
    rows: list[dict[str, object]],
    *,
    collection: str,
    asset_base_dir: Path,
    out_dir: Path,
) -> str:
    label = display_collection_name(collection)
    generated_at = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    cards: list[str] = []
    for row in rows:
        author = row.get("author", {})
        if not isinstance(author, dict):
            author = {}
        collection_meta = row.get("collection", {})
        if not isinstance(collection_meta, dict):
            collection_meta = {}
        media = row.get("media", [])
        if not isinstance(media, list):
            media = []
        article = row.get("article")
        article_media_keys = set()
        if isinstance(article, dict):
            article_media = article.get("media", [])
            if isinstance(article_media, list):
                article_media_keys = {
                    str(item.get("media_key")) for item in article_media if item.get("media_key")
                }
        tweet_media = [
            item for item in media if str(item.get("media_key")) not in article_media_keys
        ]
        urls = row.get("urls", [])
        if not isinstance(urls, list):
            urls = []
        media_html = _render_media_gallery(
            tweet_media,
            asset_base_dir=asset_base_dir,
            out_dir=out_dir,
        )
        url_html = _render_url_list(urls)
        article_html = _render_article_section(
            article if isinstance(article, dict) else None,
            asset_base_dir=asset_base_dir,
            out_dir=out_dir,
        )
        cards.append(
            """
            <article class="tweet-card">
              <header>
                <div class="author-line">
                  <strong>{display_name}</strong> <span>@{username}</span>
                </div>
                <div class="meta-line">tweet {tweet_id}</div>
              </header>
              <p class="tweet-text">{text}</p>
              {media_html}
              {url_html}
              {article_html}
              <footer>
                <span>created {created_at}</span>
                <span>synced {synced_at}</span>
                <a href="{url}">open on X</a>
              </footer>
            </article>
            """.format(
                display_name=escape(str(author.get("display_name") or "Unknown")),
                username=escape(str(author.get("username") or "unknown")),
                tweet_id=escape(str(row.get("tweet_id") or "")),
                text=escape(str(row.get("text") or "")),
                media_html=media_html,
                url_html=url_html,
                article_html=article_html,
                created_at=escape(str(row.get("created_at") or "")),
                synced_at=escape(str(collection_meta.get("synced_at") or "")),
                url=escape(tweet_url(row)),
            ).strip()
        )
    body = (
        "\n".join(cards)
        if cards
        else '<p class="empty">No archived tweets for this collection.</p>'
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>tweetxvault export: {escape(label)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f2ea;
      --panel: #fffdf7;
      --ink: #1f1b16;
      --muted: #6b6258;
      --line: #d8cfc3;
      --accent: #9d3c16;
      --accent-soft: #efe1cc;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Iowan Old Style", "Palatino Linotype", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, #efe1cc 0, transparent 28rem),
        linear-gradient(180deg, #faf7f0 0%, var(--bg) 100%);
    }}
    main {{
      max-width: 70rem;
      margin: 0 auto;
      padding: 2rem 1rem 4rem;
    }}
    .hero {{
      margin-bottom: 2rem;
      padding: 1.5rem;
      border: 1px solid var(--line);
      background: rgba(255, 253, 247, 0.9);
    }}
    .hero h1 {{
      margin: 0 0 0.5rem;
      font-size: clamp(2rem, 5vw, 3.2rem);
      line-height: 1;
    }}
    .hero p {{
      margin: 0.25rem 0;
      color: var(--muted);
    }}
    .tweet-list {{
      display: grid;
      gap: 1rem;
    }}
    .tweet-card {{
      padding: 1rem 1.2rem;
      border: 1px solid var(--line);
      background: var(--panel);
      box-shadow: 0 0.8rem 2rem rgba(68, 47, 24, 0.06);
    }}
    .author-line {{
      font-size: 1.05rem;
    }}
    .author-line span,
    .meta-line,
    .tweet-card footer,
    .empty,
    .article-meta,
    .media-card figcaption,
    .url-item span {{
      color: var(--muted);
    }}
    .meta-line {{
      margin-top: 0.25rem;
      font-family: "Courier New", monospace;
      font-size: 0.85rem;
    }}
    .tweet-text,
    .article-body {{
      white-space: pre-wrap;
      line-height: 1.5;
    }}
    .tweet-text {{
      margin: 1rem 0;
    }}
    .article-block {{
      margin-top: 1rem;
      padding: 1rem;
      border: 1px solid var(--line);
      background: linear-gradient(180deg, var(--accent-soft), rgba(255, 253, 247, 0.95));
    }}
    .article-block h2 {{
      margin: 0 0 0.5rem;
      font-size: 1.5rem;
    }}
    .article-summary {{
      margin: 0.5rem 0 1rem;
      font-style: italic;
    }}
    .article-body {{
      margin-top: 1rem;
      font-size: 0.98rem;
    }}
    .media-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
      gap: 0.75rem;
      margin: 1rem 0;
    }}
    .media-card {{
      margin: 0;
      padding: 0.6rem;
      border: 1px solid var(--line);
      background: rgba(255, 253, 247, 0.88);
    }}
    .media-thumb {{
      display: block;
      aspect-ratio: 4 / 3;
      overflow: hidden;
      border: 1px solid var(--line);
      background: #efe7db;
    }}
    .media-thumb img,
    .media-thumb .media-placeholder {{
      width: 100%;
      height: 100%;
      object-fit: cover;
    }}
    .media-placeholder {{
      display: grid;
      place-items: center;
      font-size: 0.9rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
    }}
    .media-card figcaption {{
      display: flex;
      justify-content: space-between;
      gap: 0.5rem;
      margin-top: 0.5rem;
      font-size: 0.85rem;
    }}
    .url-list {{
      display: grid;
      gap: 0.75rem;
      margin: 1rem 0;
      padding: 0;
      list-style: none;
    }}
    .url-item {{
      padding: 0.8rem;
      border: 1px solid var(--line);
      background: rgba(255, 253, 247, 0.78);
    }}
    .url-item p {{
      margin: 0.35rem 0;
    }}
    .tweet-card footer {{
      display: flex;
      flex-wrap: wrap;
      gap: 1rem;
      margin-top: 1rem;
      font-size: 0.9rem;
    }}
    a {{
      color: var(--accent);
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <h1>{escape(label)}</h1>
      <p>{len(rows)} archived tweets</p>
      <p>Generated {escape(generated_at)}</p>
    </section>
    <section class="tweet-list">
      {body}
    </section>
  </main>
</body>
</html>
"""


def export_html_archive(store: ArchiveStore, *, collection: str, out_path: Path) -> Path:
    normalized = normalize_collection_name(collection)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = store.export_rows(normalized)
    payload = _render_html_archive(
        rows,
        collection=normalized,
        asset_base_dir=store.db_path.parent,
        out_dir=out_path.parent,
    )
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=out_path.parent,
        delete=False,
        prefix=f"{out_path.name}.",
        suffix=".tmp",
    ) as handle:
        handle.write(payload)
        temp_path = Path(handle.name)
    temp_path.replace(out_path)
    return out_path
