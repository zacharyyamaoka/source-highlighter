"""`source-highlighter <source> --locations locations.json --style spotlight --out marked.png`"""

from __future__ import annotations

import argparse
import json
import sys

from .geometry import STYLES
from .render import render


def main(argv: list[str] | None = None) -> int:
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
