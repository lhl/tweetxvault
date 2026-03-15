"""HTML export."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from tweetxvault.export.common import display_collection_name, normalize_collection_name, tweet_url
from tweetxvault.storage import ArchiveStore


def _render_html_archive(rows: list[dict[str, object]], *, collection: str) -> str:
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
      max-width: 60rem;
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
    .empty {{
      color: var(--muted);
    }}
    .meta-line {{
      margin-top: 0.25rem;
      font-family: "Courier New", monospace;
      font-size: 0.85rem;
    }}
    .tweet-text {{
      margin: 1rem 0;
      white-space: pre-wrap;
      line-height: 1.5;
    }}
    .tweet-card footer {{
      display: flex;
      flex-wrap: wrap;
      gap: 1rem;
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
    payload = _render_html_archive(rows, collection=normalized)
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
