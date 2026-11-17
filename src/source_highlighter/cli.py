"""`source-highlighter <source> --locations locations.json --style spotlight --out marked.png`

Also `source-highlighter check refs.json [--out statuses.json]` (the CONTRACT §6 status of each Reference) and
`source-highlighter view refs.json [--port 0]` (the standalone viewer, on 127.0.0.1).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .geometry import STYLES
from .render import render


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "check":
        return _check(argv[1:])
    if argv and argv[0] == "view":
        return _view(argv[1:])
    return _render(argv)


def _check(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="source-highlighter check",
        description="Check each Reference against its live source (CONTRACT §6): live, deviated, dead or link-only. "
        "Polite: robots.txt, at most one request per second per host. An earlier --out file carries the failure "
        "history, so 'dead' needs three failures spread over time.",
    )
    parser.add_argument("refs", help="a reference set: a JSON list of References (CONTRACT §5)")
    parser.add_argument("--out", help="write the statuses here (and read the failure history from it, if it exists)")
    parser.add_argument("--vault", help="the vault root that source.vault paths are relative to")
    args = parser.parse_args(argv)
    from .check import run

    try:
        result = run(Path(args.refs), Path(args.out) if args.out else None, Path(args.vault) if args.vault else None)
    except (OSError, ValueError) as error:
        print(f"source-highlighter check: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _view(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="source-highlighter view",
        description="Serve the standalone viewer for a reference set on 127.0.0.1: Lens (one reference, full page, "
        "#ref=<id>) at /, and peek.js for embedding citations in any page. Prints the URL; opens no browser.",
    )
    parser.add_argument("refs", help="a reference set: a JSON list of References (CONTRACT §5)")
    parser.add_argument("--port", type=int, default=0, help="port to listen on (default 0: any free port)")
    parser.add_argument("--statuses", help="statuses from `source-highlighter check` (default: statuses.json beside the refs, if any)")
    parser.add_argument("--vault", help="the vault root that source.vault paths are relative to")
    args = parser.parse_args(argv)
    from .view import serve

    try:
        return serve(Path(args.refs), port=args.port, statuses=Path(args.statuses) if args.statuses else None, vault=Path(args.vault) if args.vault else None)
    except (OSError, ValueError) as error:
        print(f"source-highlighter view: {error}", file=sys.stderr)
        return 1


def _render(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="source-highlighter",
        description="Burn highlight / box / underline / spotlight / blackout marks into a copy of a PDF, image, HTML page or text.",
    )
    parser.add_argument("source", help="PDF, image, HTML or text/markdown file")
    parser.add_argument("--locations", required=True, help="JSON: a list of Locations, {locations}, or {layers}")
    parser.add_argument("--style", choices=STYLES, default="highlight")
    parser.add_argument("--color", help="any CSS / Pillow colour, e.g. '#ffe600' or 'red'")
    parser.add_argument("--opacity", type=float)
    parser.add_argument("--padding", type=float, help="in source units (PDF points, image px, CSS px)")
    parser.add_argument("--width", type=float, help="stroke width of box / underline, in source units")
    parser.add_argument("--label", action="store_true", help="draw each Location's label beside its mark")
    parser.add_argument("--page", type=int, help="PDF: render only this page (1-based)")
    parser.add_argument("--scale", type=float, help="output pixels per source unit (PDF default 2)")
    parser.add_argument("--twin", help="HTML: draw on this visual-twin screenshot instead of laying the page out")
    parser.add_argument("--pdf", dest="pdf_out", help="PDF: also write a vector-marked PDF here")
    parser.add_argument("--layout-width", type=int, help="HTML/text: layout width in CSS px")
    parser.add_argument("--markdown", choices=("raw", "rendered"), default="raw",
                        help="text/markdown: show it raw (default) or rendered as Obsidian does (like-obsidian)")
    parser.add_argument("--out", required=True, help=".png / .jpg / .webp, .pdf for PDFs, .html for text")
    args = parser.parse_args(argv)

    style = {"name": args.style, "color": args.color, "opacity": args.opacity, "padding": args.padding, "width": args.width}
    if args.label:
        style["label"] = True
    try:
        result = render(
            args.source,
            args.locations,
            style,
            args.out,
            page=args.page,
            scale=args.scale,
            twin=args.twin,
            pdf_out=args.pdf_out,
            width=args.layout_width,
            markdown=args.markdown,
        )
    except (OSError, ValueError) as error:
        print(f"source-highlighter: {error}", file=sys.stderr)
        return 1
    for entry in result.unresolved:
        print(f"source-highlighter: unresolved location {entry.layer}:{entry.index}: {entry.reason}", file=sys.stderr)
    print(json.dumps(result.as_dict(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
