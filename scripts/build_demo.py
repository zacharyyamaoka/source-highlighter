"""Assemble demo/ so `python3 -m http.server --directory demo` serves everything it needs.

Copies the ES module, PDF.js (from js/node_modules — run `pnpm --dir js install` first) and the test sources
into demo/lib, demo/vendor and demo/assets (all gitignored), and writes demo/assets/sources.json listing the
sources that were found. The eRob80 and CubeMars screenshots are too big for the repo; they are copied from the
snap folder when present (SOURCE_HIGHLIGHTER_SNAP or --snap) and simply left out of the demo when not.

Run: uv run python scripts/build_demo.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_fixtures import DEFAULT_SNAP

REPO = Path(__file__).resolve().parents[1]
DEMO = REPO / "demo"
FIXTURES = REPO / "tests" / "fixtures"


def copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snap", default=DEFAULT_SNAP)
    snap = Path(parser.parse_args().snap)

    copy(REPO / "js" / "source-highlighter.js", DEMO / "lib" / "source-highlighter.js")
    pdfjs_build = REPO / "js" / "node_modules" / "pdfjs-dist" / "build"
    if not pdfjs_build.exists():
        sys.exit("PDF.js missing: run `pnpm --dir js install` first")
    for name in ("pdf.min.mjs", "pdf.worker.min.mjs"):
        copy(pdfjs_build / name, DEMO / "vendor" / "pdfjs" / name)

    assets = DEMO / "assets"
    sources = [
        {
            "id": "pdf",
            "title": "J60 Joint datasheet (PDF)",
            "kind": "pdf",
            "url": "assets/j60-joint.pdf",
            "locations": "assets/j60-joint.locations.json",
            "files": [FIXTURES / "j60-joint.pdf", FIXTURES / "j60-joint.locations.json"],
        },
        {
            "id": "image",
            "title": "eRob80 page screenshot (image)",
            "kind": "image",
            "url": "assets/erob80.jpg",
            "locations": "assets/erob80.locations.json",
            "files": [snap / "eRob80.html.jpg", FIXTURES / "erob80.locations.json"],
            "rename": {"eRob80.html.jpg": "erob80.jpg"},
        },
        {
            "id": "html",
            "title": "CubeMars AK80-9 archived page (HTML)",
            "kind": "html",
            "url": "assets/cubemars-ak80-9.html",
            "locations": "assets/cubemars-ak80-9.html.locations.json",
            "files": [FIXTURES / "cubemars-ak80-9.html", FIXTURES / "cubemars-ak80-9.html.locations.json"],
        },
        {
            "id": "twin",
            "title": "CubeMars AK80-9 visual twin (HTML → screenshot)",
            "kind": "image",
            "url": "assets/cubemars-ak80-9.jpg",
            "locations": "assets/cubemars-ak80-9.twin.locations.json",
            "files": [snap / "ak80-9-v3-0-robotic-actuator.html.jpg", FIXTURES / "cubemars-ak80-9.twin.locations.json"],
            "rename": {"ak80-9-v3-0-robotic-actuator.html.jpg": "cubemars-ak80-9.jpg"},
        },
        {
            "id": "text",
            "title": "CubeMars spec as markdown (text)",
            "kind": "text",
            "url": "assets/cubemars-ak80-9-spec.md",
            "locations": "assets/cubemars-ak80-9-spec.md.locations.json",
            "files": [FIXTURES / "cubemars-ak80-9-spec.md", FIXTURES / "cubemars-ak80-9-spec.md.locations.json"],
        },
    ]
    available = []
    for source in sources:
        files = source.pop("files")
        renames = source.pop("rename", {})
        missing = [path for path in files if not path.exists()]
        if missing:
            print(f"skipped {source['id']}: missing {', '.join(map(str, missing))}")
            continue
        for path in files:
            copy(path, assets / renames.get(path.name, path.name))
        available.append(source)
    (assets / "sources.json").write_text(json.dumps(available, indent=2) + "\n", encoding="utf-8")
    print(f"demo ready with {len(available)} sources: {', '.join(source['id'] for source in available)}")


if __name__ == "__main__":
    main()
