"""JSON export."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tweetxvault.export.common import normalize_collection_name
from tweetxvault.storage import ArchiveStore


def export_json_archive(store: ArchiveStore, *, collection: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = store.export_rows(normalize_collection_name(collection))
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
