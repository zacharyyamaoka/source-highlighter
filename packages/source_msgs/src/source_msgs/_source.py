"""Source — one cited thing (a web page, a PDF, an image, a markdown note), however it was made (PEP 0001).

Every constructor gives the same frozen type. Its JSON form (`to_dict`) is a CONTRACT §5 Reference `source`, so the
Reference sets callers already store read straight into it, and back.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .highlighted import Highlighted

KINDS = ("pdf", "html", "image", "text")
# Capture levels besides the primary bytes (markdownify CONTRACT §2.1), and the files an mdify folder keeps them in.
LEVEL_FILES = {"snapshot": "snapshot.jpg", "pdf": "snapshot.pdf", "single": "single.html", "archive": "source.wacz"}
_EXTENSIONS = {"application/pdf": "pdf", "text/html": "html", "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
               "image/gif": "gif", "text/markdown": "md", "text/plain": "txt"}
_IMAGE_SUFFIXES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
                   ".gif": "image/gif", ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff"}
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z")


class FrozenDict(Mapping[str, str]):
    """An immutable, hashable str → str mapping (robot_msgs' Header.meta has the same one)."""

    __slots__ = ("_items",)

    def __init__(self, items: Mapping[str, str] | Iterable[tuple[str, str]] = ()):
        self._items = dict(items)

    def __getitem__(self, key: str) -> str:
        return self._items[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __hash__(self) -> int:
        return hash(frozenset(self._items.items()))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Mapping) and dict(self._items) == dict(other)

    def __repr__(self) -> str:
        return f"FrozenDict({self._items!r})"


def kind_of(media_type: str) -> str:
    if media_type == "application/pdf":
        return "pdf"
    if media_type.startswith("image/"):
        return "image"
    if media_type in ("text/html", "application/xhtml+xml"):
        return "html"
    return "text"


def sniff_media_type(data: bytes, name: str = "") -> str:
    """The media type from the bytes first, then the name. WHY bytes first: cached blobs have no extension, and a
    PDF served as octet-stream is still a PDF."""
    head = data[:1024]
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"GIF8"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    suffix = Path(name).suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        return _IMAGE_SUFFIXES[suffix]
    if suffix in (".html", ".htm", ".xhtml") or re.match(rb"\s*(<!doctype html|<html)", head, re.IGNORECASE):
        return "text/html"
    if suffix in (".md", ".markdown"):
        return "text/markdown"
    return "text/plain"


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def cache_root() -> Path:
    """Where `from_text` and a stdlib `capture()` save bytes: $SOURCE_MSGS_CACHE, else ~/.cache/source-msgs."""
    return Path(os.environ.get("SOURCE_MSGS_CACHE") or Path.home() / ".cache" / "source-msgs")


def _save(data: bytes, media_type: str) -> tuple[str, Path]:
    """Content-addressed: the same bytes always land in the same file."""
    sha256 = hashlib.sha256(data).hexdigest()
    path = cache_root() / f"{sha256}.{_EXTENSIONS.get(media_type, 'bin')}"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(path.suffix + ".partial")
        partial.write_bytes(data)
        partial.replace(path)
    return sha256, path


def _is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


@dataclass(frozen=True)
class Source:
    """A cited source and what was saved of it.

    origin: where it came from — a URL, or the absolute path of a file (CONTRACT §5's `url`).
    kind: 'pdf' | 'html' | 'image' | 'text' (markdown and plain text) — what the highlighter draws on.
    media_type: 'application/pdf', 'text/html', 'image/png', 'text/markdown', …
    sha256: of the saved bytes; None while the Source is a link only.
    path: the saved bytes on disk (a file source is saved as itself).
    retrieved_at: when the bytes were read or fetched, ISO 8601 UTC ('…Z').
    stored: the other capture levels kept (snapshot, pdf, single, archive) → their paths.
    title: a human name for it.
    """

    origin: str
    kind: str
    media_type: str
    sha256: str | None = None
    path: str | None = None
    retrieved_at: str | None = None
    stored: Mapping[str, str] = field(default_factory=FrozenDict)
    title: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"Source.kind must be one of {', '.join(KINDS)}, got {self.kind!r}")
        if not isinstance(self.origin, str) or not self.origin:
            raise ValueError("Source.origin (a URL or a path) is required")
        if self.sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError(f"Source.sha256 must be 64 lowercase hex digits, got {self.sha256!r}")
        if (self.sha256 is None) != (self.path is None):
            raise ValueError("Source.sha256 and Source.path go together: saved bytes have both, a link has neither")
        if self.retrieved_at is not None and not _TIMESTAMP.fullmatch(self.retrieved_at):
            raise ValueError(f"Source.retrieved_at must be ISO 8601 UTC like 2026-11-16T20:00:00Z, got {self.retrieved_at!r}")
        for key, value in self.stored.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError(f"Source.stored maps a level name to a path string, got {key!r}: {value!r}")
        object.__setattr__(self, "stored", FrozenDict(self.stored))

    # --- identity -------------------------------------------------------------------------------------------------

    @property
    def id(self) -> str:
        """The sha256 of the saved bytes; for a link only, the sha256 of its origin (other content, other id)."""
        return self.sha256 or hashlib.sha256(self.origin.encode("utf-8")).hexdigest()

    @property
    def is_saved(self) -> bool:
        return self.path is not None

    @property
    def size(self) -> int | None:
        return Path(self.path).stat().st_size if self.path else None

    def read_bytes(self) -> bytes:
        if self.path is None:
            raise ValueError(f"{self.origin}: no saved bytes yet (a link only); call .capture() first")
        return Path(self.path).read_bytes()

    def text(self) -> str:
        """The decoded text of an html or text source (what TextPosition offsets count into)."""
        raw = self.read_bytes()
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            declared = re.search(rb"charset=[\"']?([A-Za-z0-9_\-]+)", raw[:4096])
            return raw.decode(declared.group(1).decode() if declared else "latin-1", errors="replace")

    # --- constructors: many entry points, one output ----------------------------------------------------------------

    @classmethod
    def from_file(cls, path: str | os.PathLike, *, origin: str | None = None, title: str | None = None) -> Source:
        """A file on disk, saved as itself: read, hashed, its kind sniffed from the bytes."""
        resolved = Path(path).expanduser().resolve()
        data = resolved.read_bytes()
        media_type = sniff_media_type(data, resolved.name)
        return cls(origin=origin or str(resolved), kind=kind_of(media_type), media_type=media_type,
                   sha256=hashlib.sha256(data).hexdigest(), path=str(resolved), retrieved_at=_now(),
                   title=title if title is not None else resolved.stem)

    @classmethod
    def from_text(cls, text: str, media_type: str = "text/markdown", *, origin: str | None = None,
                  title: str | None = None) -> Source:
        """Text in memory, saved content-addressed under cache_root() so it can be cited and drawn like a file."""
        if media_type not in ("text/markdown", "text/plain", "text/html"):
            raise ValueError(f"from_text takes text/markdown, text/plain or text/html, got {media_type!r}")
        sha256, path = _save(text.encode("utf-8"), media_type)
        return cls(origin=origin or str(path), kind=kind_of(media_type), media_type=media_type, sha256=sha256,
                   path=str(path), retrieved_at=_now(), title=title)

    @classmethod
    def from_url(cls, url: str, *, title: str | None = None) -> Source:
        """A link only: no network. Its kind is guessed from the URL until `.capture()` reads the bytes."""
        if Path(url).is_absolute() or url.startswith("file://"):
            local = Path(url.removeprefix("file://"))
            return cls(origin=str(local), kind=kind_of(sniff_media_type(b"", local.name)),
                       media_type=sniff_media_type(b"", local.name), title=title)
        if not _is_url(url):
            raise ValueError(f"from_url takes an http(s) URL or an absolute path, got {url!r}")
        suffix = Path(url.split("?")[0].split("#")[0]).suffix.lower()
        media_type = "application/pdf" if suffix == ".pdf" else _IMAGE_SUFFIXES.get(suffix, "text/html")
        return cls(origin=url, kind=kind_of(media_type), media_type=media_type, title=title)

    @classmethod
    def from_bundle(cls, directory: str | os.PathLike, *, title: str | None = None) -> Source:
        """A folder written by `mdify capture` or `mdify`: source.json + source.<ext> (+ snapshot, pdf, archive)."""
        folder = Path(directory).expanduser().resolve()
        record = json.loads((folder / "source.json").read_text(encoding="utf-8"))
        retrieval = (record.get("retrievals") or [{}])[-1]
        primary = next((path for path in sorted(folder.glob("source.*"))
                        if path.suffix not in (".json", ".wacz")), None)
        if primary is None:
            raise ValueError(f"{folder}: no source.<ext> (a link-only capture keeps only source.json)")
        stored = {level: str(folder / name) for level, name in LEVEL_FILES.items() if (folder / name).is_file()}
        media_type = record.get("media_type") or sniff_media_type(primary.read_bytes(), primary.name)
        origin = retrieval.get("url") or retrieval.get("final_url") or retrieval.get("local_file") or str(primary)
        return cls(origin=origin, kind=kind_of(media_type), media_type=media_type,
                   sha256=record.get("sha256") or hashlib.sha256(primary.read_bytes()).hexdigest(), path=str(primary),
                   retrieved_at=retrieval.get("retrieved_at"), stored=stored, title=title)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Source:
        """`to_dict()`'s output, or a CONTRACT §5 Reference `source` ({url, retrieved_at, sha256, media_type, stored})."""
        stored = dict(data.get("stored") or {})
        origin = data.get("origin") or data.get("url")
        if not origin:
            raise ValueError("a source needs its url (or origin)")
        media_type = data.get("media_type")
        path = data.get("path")
        if path is None:
            # CONTRACT §5: a web page's bytes are its stored html; a local file source is stored as itself.
            path = stored.get("html") or (origin if Path(origin).is_absolute() and data.get("sha256") else None)
        if media_type is None:
            media_type = sniff_media_type(Path(path).read_bytes() if path else b"", path or origin)
        stored.pop("html", None)
        return cls(origin=origin, kind=data.get("kind") or kind_of(media_type), media_type=media_type,
                   sha256=data.get("sha256") if path else None, path=path, retrieved_at=data.get("retrieved_at"),
                   stored=stored, title=data.get("title"))

    def to_dict(self) -> dict[str, Any]:
        """A CONTRACT §5 Reference `source`, plus `kind`, `path` and `title`. JSON-ready."""
        stored = dict(self.stored)
        if self.kind == "html" and self.path:
            stored["html"] = self.path
        data = {"url": self.origin, "kind": self.kind, "media_type": self.media_type, "sha256": self.sha256,
                "path": self.path, "retrieved_at": self.retrieved_at, "stored": stored, "title": self.title}
        return {key: value for key, value in data.items() if value is not None}

    # --- verbs ------------------------------------------------------------------------------------------------------

    def capture(self, keep: tuple[str, ...] = ("html", "snapshot"), *, using: str = "auto") -> Source:
        """Save the bytes. A no-op once saved; a local path is read. A web page goes through `mdify capture` when mdify
        is installed (`using="mdify"`: every capture level in `keep`, the snapshot's word boxes), else one polite
        stdlib request (`using="stdlib"`: robots.txt honoured, refused hosts never asked) saved under cache_root()."""
        if self.is_saved:
            return self
        if not _is_url(self.origin):
            return replace(Source.from_file(self.origin), title=self.title or Path(self.origin).stem)
        if using not in ("auto", "mdify", "stdlib"):
            raise ValueError(f"using must be 'auto', 'mdify' or 'stdlib', got {using!r}")
        if using in ("auto", "mdify") and self.kind == "html":
            try:
                from mdify.capture import capture as mdify_capture
            except ImportError:
                if using == "mdify":
                    raise ImportError("capture(using='mdify') needs mdify: pip install 'source-msgs[capture]'") from None
            else:
                captured = mdify_capture(self.origin, tuple(keep))
                if captured.directory is None:
                    raise RuntimeError(f"{self.origin}: mdify kept nothing")
                return replace(Source.from_bundle(captured.directory), origin=self.origin, title=self.title)
        from ._fetch import Fetcher, FetchError, FetchRefused, media_type_of

        try:
            fetched = Fetcher().fetch(self.origin)
        except FetchRefused as refused:
            raise PermissionError(f"{self.origin}: not fetched ({refused}; robots.txt and refused hosts are honoured)") from None
        except FetchError as error:
            raise ConnectionError(f"{self.origin}: {error}") from None
        media_type = media_type_of(fetched.content_type, fetched.body)
        sha256, path = _save(fetched.body, media_type)
        return Source(origin=self.origin, kind=kind_of(media_type), media_type=media_type, sha256=sha256,
                      path=str(path), retrieved_at=_now(), title=self.title)

    def to_markdown(self) -> str:
        """Markdown of the source. Markdown and plain text are read as they are; a PDF, page or image goes through
        mdify (pipeline A, no model) when it is installed."""
        source = self.capture() if not self.is_saved else self
        if source.kind == "text":
            return source.text()
        try:
            from mdify.bundle import build
        except ImportError:
            raise ImportError(
                f"to_markdown() of a{'n' if source.kind in ('html', 'image') else ''} {source.kind} source needs mdify: "
                "pip install 'source-msgs[markdown]'"
            ) from None
        built = build(source.path)
        return (Path(built.directory) / "document.md").read_text(encoding="utf-8")

    def highlight(self, locations: Any, style: str | Mapping[str, Any] | None = "highlight", **style_options: Any) -> Highlighted:
        """This source + locations + style, as data: nothing is drawn until `.show()` or `.save()`.

        locations: one or a list of selectors (PageBox, TextQuote, …), compact strings ("page=2&xywh=percent:…"),
        CONTRACT §2 dicts, or {"layers": [{"locations", "style"}]}. style: a style name or {name, color, …};
        keyword options (color=, opacity=, padding=, width=, label=) are merged into it.
        """
        from .highlighted import Highlighted

        return Highlighted.of(self, locations, style, **style_options)
