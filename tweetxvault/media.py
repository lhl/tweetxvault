"""Media download helpers."""

from __future__ import annotations

import mimetypes
import re
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from rich.console import Console

from tweetxvault.config import DEFAULT_USER_AGENT, AppConfig, XDGPaths
from tweetxvault.jobs import locked_archive_job
from tweetxvault.utils import utc_now

_CONTENT_TYPE_EXTENSIONS = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
}
_UPDATE_BATCH_SIZE = 100


@dataclass(slots=True)
class MediaDownloadResult:
    processed: int = 0
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0


def _safe_media_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _content_extension(content_type: str | None, url: str) -> str:
    if content_type:
        base_type = content_type.split(";", 1)[0].strip().lower()
        if base_type in _CONTENT_TYPE_EXTENSIONS:
            return _CONTENT_TYPE_EXTENSIONS[base_type]
        guessed = mimetypes.guess_extension(base_type)
        if guessed:
            return ".jpg" if guessed == ".jpe" else guessed
    path = urlsplit(url).path
    suffix = Path(path).suffix.lower()
    return suffix or ".bin"


def _photo_download_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.netloc != "pbs.twimg.com":
        return url
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["name"] = "orig"
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def _asset_url(row: dict[str, object]) -> str | None:
    media_type = row.get("media_type")
    media_url = row.get("media_url")
    if not isinstance(media_url, str) or not media_url:
        return None
    if media_type == "photo":
        return _photo_download_url(media_url)
    return media_url


def _poster_url(row: dict[str, object]) -> str | None:
    media_type = row.get("media_type")
    if media_type not in {"video", "animated_gif"}:
        return None
    thumbnail_url = row.get("thumbnail_url")
    return thumbnail_url if isinstance(thumbnail_url, str) and thumbnail_url else None


def _resolve_path(base_dir: Path, relative_path: str | None) -> Path | None:
    if not relative_path:
        return None
    return base_dir / relative_path


def _download_complete(row: dict[str, object], base_dir: Path) -> bool:
    if row.get("download_state") != "done":
        return False
    local_path = _resolve_path(base_dir, row.get("local_path"))
    if local_path is None or not local_path.exists():
        return False
    if row.get("media_type") not in {"video", "animated_gif"}:
        return True
    poster_path = _resolve_path(base_dir, row.get("thumbnail_local_path"))
    return poster_path is not None and poster_path.exists()


def _target_path(
    base_dir: Path, row: dict[str, object], *, url: str, suffix: str
) -> tuple[str, Path]:
    tweet_id = str(row.get("tweet_id") or "unknown")
    media_key = _safe_media_stem(str(row.get("media_key") or "media"))
    extension = _content_extension(None, url)
    relative = Path("media") / tweet_id / f"{media_key}{suffix}{extension}"
    return relative.as_posix(), base_dir / relative


async def _download_file(
    client: httpx.AsyncClient,
    *,
    url: str,
    destination: Path,
) -> tuple[Path, str, int, str | None]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    content_type: str | None = None
    file_hash = sha256()
    byte_size = 0
    with tempfile.NamedTemporaryFile(
        "wb",
        dir=destination.parent,
        delete=False,
        prefix=f"{destination.name}.",
        suffix=".tmp",
    ) as handle:
        temp_path = Path(handle.name)
        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type")
                async for chunk in response.aiter_bytes():
                    handle.write(chunk)
                    file_hash.update(chunk)
                    byte_size += len(chunk)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
    final_destination = destination
    if content_type:
        extension = _content_extension(content_type, url)
        if destination.suffix.lower() != extension:
            final_destination = destination.with_suffix(extension)
    final_destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path.replace(final_destination)
    return final_destination, file_hash.hexdigest(), byte_size, content_type


async def download_media(
    *,
    limit: int | None = None,
    photos_only: bool = False,
    retry_failed: bool = False,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
    console: Console | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> MediaDownloadResult:
    async with locked_archive_job(config=config, paths=paths) as job:
        config = job.config
        paths = job.paths
        store = job.store
        states = {"pending"}
        if retry_failed:
            states.add("failed")
        media_types = {"photo"} if photos_only else None
        rows = store.list_media_rows(states=states, media_types=media_types, limit=limit)
        result = MediaDownloadResult()
        if not rows:
            return result

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=max(config.sync.timeout, 60.0),
            transport=transport,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        ) as client:
            pending_updates: list[dict[str, object]] = []

            def flush_updates() -> None:
                if not pending_updates:
                    return
                store.merge_rows(pending_updates.copy())
                pending_updates.clear()

            for row in rows:
                result.processed += 1
                if _download_complete(row, paths.data_dir):
                    result.skipped += 1
                    continue

                state = {
                    "local_path": row.get("local_path"),
                    "sha256": row.get("sha256"),
                    "byte_size": row.get("byte_size"),
                    "content_type": row.get("content_type"),
                    "thumbnail_local_path": row.get("thumbnail_local_path"),
                    "thumbnail_sha256": row.get("thumbnail_sha256"),
                    "thumbnail_byte_size": row.get("thumbnail_byte_size"),
                    "thumbnail_content_type": row.get("thumbnail_content_type"),
                }
                try:
                    asset_url = _asset_url(row)
                    if asset_url is None:
                        raise ValueError("missing media download URL")
                    _, destination = _target_path(
                        paths.data_dir,
                        row,
                        url=asset_url,
                        suffix="",
                    )
                    final_path, file_hash, byte_size, content_type = await _download_file(
                        client,
                        url=asset_url,
                        destination=destination,
                    )
                    state.update(
                        {
                            "local_path": final_path.relative_to(paths.data_dir).as_posix(),
                            "sha256": file_hash,
                            "byte_size": byte_size,
                            "content_type": content_type,
                        }
                    )

                    poster_url = _poster_url(row)
                    if poster_url:
                        _, poster_destination = _target_path(
                            paths.data_dir,
                            row,
                            url=poster_url,
                            suffix="-poster",
                        )
                        (
                            poster_path,
                            poster_hash,
                            poster_size,
                            poster_content_type,
                        ) = await _download_file(
                            client,
                            url=poster_url,
                            destination=poster_destination,
                        )
                        state.update(
                            {
                                "thumbnail_local_path": poster_path.relative_to(
                                    paths.data_dir
                                ).as_posix(),
                                "thumbnail_sha256": poster_hash,
                                "thumbnail_byte_size": poster_size,
                                "thumbnail_content_type": poster_content_type,
                            }
                        )

                    pending_updates.append(
                        store.build_media_download_update(
                            row,
                            download_state="done",
                            local_path=state["local_path"],
                            sha256=state["sha256"],
                            byte_size=state["byte_size"],
                            content_type=state["content_type"],
                            thumbnail_local_path=state["thumbnail_local_path"],
                            thumbnail_sha256=state["thumbnail_sha256"],
                            thumbnail_byte_size=state["thumbnail_byte_size"],
                            thumbnail_content_type=state["thumbnail_content_type"],
                            downloaded_at=utc_now(),
                            download_error=None,
                        )
                    )
                    result.downloaded += 1
                except Exception as exc:
                    pending_updates.append(
                        store.build_media_download_update(
                            row,
                            download_state="failed",
                            local_path=state["local_path"],
                            sha256=state["sha256"],
                            byte_size=state["byte_size"],
                            content_type=state["content_type"],
                            thumbnail_local_path=state["thumbnail_local_path"],
                            thumbnail_sha256=state["thumbnail_sha256"],
                            thumbnail_byte_size=state["thumbnail_byte_size"],
                            thumbnail_content_type=state["thumbnail_content_type"],
                            downloaded_at=row.get("downloaded_at") or None,
                            download_error=str(exc),
                        )
                    )
                    result.failed += 1
                    if console:
                        console.print(f"media {row['row_key']}: failed ({exc})", highlight=False)
                if len(pending_updates) >= _UPDATE_BATCH_SIZE:
                    flush_updates()

            flush_updates()

        if result.processed > 0:
            job.mark_dirty()
        return result
