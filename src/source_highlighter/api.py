"""The Source API's drawing half (PEP 0001 §5): `Highlighted.save()` and `.show()` land here.

source_msgs holds the types (stdlib only); this module turns a Highlighted into the CONTRACT §2 layers `render()` has
always drawn, so a selector and the dict it stands for draw the same pixels (tests/test_source_api.py).
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import sys
import tempfile
import webbrowser
from pathlib import Path
from typing import Any

from source_msgs import Highlighted, Source

from .geometry import RenderResult, Unresolved
from .render import render

BACKENDS = ("browser", "notebook", "none")


def as_source(source: Any) -> Source:
    """A Source as is; an http(s) URL as a link (captured when drawn); anything else as a file path."""
    if isinstance(source, Source):
        return source
    text = str(source)
    return Source.from_url(text) if text.startswith(("http://", "https://")) else Source.from_file(text)


def highlight(source: Any, locations: Any, style: Any = "highlight", **style_options: Any) -> Highlighted:
    """The one-liner: `highlight("datasheet.pdf", "page=2&xywh=percent:61,40,7,2", "box").show()`."""
    return as_source(source).highlight(locations, style, **style_options)


def _layers_for_render(highlighted: Highlighted, kind: str | None = None) -> tuple[list[dict], list[list[int]], list[Unresolved]]:
    """CONTRACT §2 layers, each drawn index's original position, and what could not be converted."""
    layers, unconvertible = highlighted.contract_layers(kind)
    positions: list[list[int]] = []
    skipped = {(layer, index) for layer, index, _, _ in unconvertible}
    for layer_index, layer in enumerate(layers):
        original = [index for index in range(len(highlighted.layers[layer_index].locations)) if (layer_index, index) not in skipped]
        positions.append(original)
        # WHY explicit ids: a mark's default id is "<layer>:<index>", and the index must be the caller's, not the
        # index after unconvertible selectors were taken out.
        layer["locations"] = [
            location if "id" in location else {**location, "id": f"{layer_index}:{index}"}
            for location, index in zip(layer["locations"], original)
        ]
    return layers, positions, [Unresolved(layer, index, location, reason) for layer, index, location, reason in unconvertible]


def save(highlighted: Highlighted, path: str | Path, **render_options: Any) -> RenderResult:
    source = highlighted.source if highlighted.source.is_saved else highlighted.source.capture()
    snapshot = source.stored.get("snapshot")
    on_snapshot = source.kind == "html" and snapshot and "twin" not in render_options and highlighted.geometry_only
    if on_snapshot:
        # Geometry-anchored Locations on an HTML page are boxes on its capture-time screenshot (markdownify
        # CONTRACT §3.3): the page is drawn as that picture, its visual twin.
        render_options["twin"] = snapshot
    layers, positions, unconvertible = _layers_for_render(highlighted, "image" if on_snapshot else None)
    result = render(source.path, {"layers": layers}, None, path, **render_options)
    for entry in result.unresolved:
        if 0 <= entry.layer < len(positions) and 0 <= entry.index < len(positions[entry.layer]):
            entry.index = positions[entry.layer][entry.index]
    result.unresolved.extend(unconvertible)
    result.unresolved.sort(key=lambda entry: (entry.layer, entry.index))
    return result


def _in_notebook() -> bool:
    shell = sys.modules.get("IPython") and getattr(sys.modules["IPython"], "get_ipython", lambda: None)()
    return type(shell).__name__ == "ZMQInteractiveShell"


def resolve_backend(backend: str | None) -> str:
    """Like matplotlib's MPLBACKEND: explicit, else $SOURCE_HIGHLIGHTER_BACKEND, else notebook / browser / none.

    WHY 'none' under pytest and without a display: a test or a headless agent must never open a window (Zach's
    no-focus-steal rule); `.save()` is the headless path and `.show()` falls back to it.
    """
    chosen = backend or os.environ.get("SOURCE_HIGHLIGHTER_BACKEND")
    if chosen:
        if chosen not in BACKENDS:
            raise ValueError(f"unknown backend {chosen!r} (expected {', '.join(BACKENDS)})")
        return chosen
    if "pytest" in sys.modules:
        return "none"
    if _in_notebook():
        return "notebook"
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return "none"
    return "browser"


def _show_dir() -> Path:
    return Path(os.environ.get("SOURCE_HIGHLIGHTER_SHOW_DIR") or Path(tempfile.gettempdir()) / "source-highlighter-show")


def _page(highlighted: Highlighted, result: RenderResult, images: list[Path]) -> str:
    source = highlighted.source
    title = html.escape(source.title or Path(source.origin).name or source.origin)
    origin = html.escape(source.origin)
    marks = "".join(
        f"<li><code>{html.escape(mark.id)}</code> {html.escape(mark.label or '')} — page {mark.page}, {mark.style}</li>"
        for mark in result.marks
    )
    unresolved = "".join(
        f"<li><code>{html.escape(json.dumps(entry.location, ensure_ascii=False))}</code>: {html.escape(entry.reason)}</li>"
        for entry in result.unresolved
    )
    pictures = "".join(f'<img src="{html.escape(image.name)}" alt="{title}, highlighted">' for image in images)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title} — highlighted</title>
<style>body{{margin:0;font:14px/1.5 system-ui,sans-serif;background:#f4f4f2;color:#1d1d1b}}header{{padding:12px 16px;
background:#fff;border-bottom:1px solid #ddd}}main{{padding:16px;display:grid;gap:16px;justify-items:center}}
img{{max-width:100%;box-shadow:0 1px 6px #0002;background:#fff}}aside{{max-width:900px;width:100%}}code{{font-size:12px}}</style>
</head><body><header><strong>{title}</strong> · <a href="{origin}">{origin}</a> · {len(result.marks)} marks, {len(result.unresolved)} unresolved</header>
<main>{pictures}<aside><ul>{marks}</ul>{f'<h3>Not drawn</h3><ul>{unresolved}</ul>' if unresolved else ''}</aside></main></body></html>
"""


def show(highlighted: Highlighted, *, backend: str | None = None, **render_options: Any) -> Path:
    chosen = resolve_backend(backend)
    key = hashlib.sha256(repr((highlighted.source.id, [(layer.locations, layer.style) for layer in highlighted.layers],
                               sorted(render_options.items()))).encode()).hexdigest()[:12]
    stem = (highlighted.source.title or "source").replace("/", "_")[:60]
    folder = _show_dir() / f"{stem}-{key}"
    folder.mkdir(parents=True, exist_ok=True)
    result = save(highlighted, folder / f"{stem}.png", **render_options)
    images = [Path(output) for output in result.outputs]
    page = folder / "index.html"
    page.write_text(_page(highlighted, result, images), encoding="utf-8")
    if chosen == "notebook":
        from IPython.display import Image, display

        for image in images:
            display(Image(data=image.read_bytes(), format="png"))
    elif chosen == "browser":
        webbrowser.open(page.as_uri(), new=2, autoraise=False)
    else:
        print(f"source-highlighter: wrote {page} (backend none: nothing opened)", file=sys.stderr)
    return page


def png_bytes(highlighted: Highlighted, **render_options: Any) -> bytes:
    with tempfile.TemporaryDirectory(prefix="source-highlighter-") as scratch:
        result = save(highlighted, Path(scratch) / "out.png", **render_options)
        return Path(result.outputs[0]).read_bytes()
