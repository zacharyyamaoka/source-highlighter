"""Reference sets (CONTRACT §5): what a caller stores per citation, and where its stored copies are on disk."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote, urlparse
from typing import Any

# CONTRACT §6: the stored copy a deviated or dead reference falls back on, best first.
LEVELS = ("html", "snapshot", "archive", "pdf")


def load_refs(path: str | Path) -> list[dict[str, Any]]:
    """A reference set: a JSON list of References. Each needs an id and a source (url, or vault)."""
    refs = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(refs, list):
        raise ValueError(f"{path}: a reference set is a JSON list of References")  # noqa: TRY004 — a bad file, reported like one
    seen: set[str] = set()
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict) or not ref.get("id") or not isinstance(ref.get("source"), dict):
            raise ValueError(f"{path}: reference {index} needs an id and a source")
        if ref["id"] in seen:
            raise ValueError(f"{path}: reference id {ref['id']!r} is used twice")
        seen.add(ref["id"])
        source = ref["source"]
        if not source.get("url") and not source.get("vault"):
            raise ValueError(f"{path}: reference {ref['id']!r} needs source.url or source.vault")
    return refs


def resolve_path(value: str, base: Path) -> Path:
    """A stored path as written in refs.json: absolute (CONTRACT §5), or relative to the refs.json it is in."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def stored_path(ref: dict, level: str, base: Path) -> Path | None:
    value = (ref["source"].get("stored") or {}).get(level)
    return resolve_path(value, base) if value else None


def vault_path(ref: dict, vault: Path | None) -> Path | None:
    """A vault note's file, under the vault root. Never outside it."""
    relative = ref["source"].get("vault")
    if not relative or vault is None:
        return None
    root = vault.resolve()
    path = (root / relative).resolve()
    return path if path.is_relative_to(root) else None


def is_local(url: str | None) -> bool:
    """CONTRACT §5: source.url may be an absolute local file path (no file:// needed)."""
    return bool(url) and url.startswith(("/", "file://"))


def local_path(url: str) -> Path:
    """A file:// URL is percent-encoded (RFC 8089), so its path is decoded; a bare absolute path is taken literally.
    WHY: ObjectDB's vault refs (`file:///…/Apple%20Notes/…`) read as missing while the files existed."""
    if url.startswith("file://"):
        return Path(unquote(urlparse(url).path))
    return Path(url)


def kept_levels(ref: dict, base: Path) -> list[str]:
    """The stored levels whose files exist, in fallback order."""
    return [level for level in LEVELS if (path := stored_path(ref, level, base)) is not None and path.is_file()]
