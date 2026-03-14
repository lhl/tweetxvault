"""JSON export."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from tweetxvault.storage import ArchiveStore


def default_export_path(base_dir: Path, collection: str) -> Path:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    return base_dir / f"export-{collection}-{stamp}.json"


def export_json_archive(store: ArchiveStore, *, collection: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = store.export_rows(collection)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=out_path.parent,
        delete=False,
        prefix=f"{out_path.name}.",
        suffix=".tmp",
    ) as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(out_path)
    return out_path
