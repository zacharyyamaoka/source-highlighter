"""Cut the committed bands out of the two real page screenshots, so their tests run in a clean clone, nothing skipped.

    uv run python scripts/crop_snapshots.py [--snap DIR]

The eRob80 page screenshot (1280 x 11540) and the CubeMars AK80-9 visual twin (1280 x 16232) are 1.2-1.4 MB each, too
big to commit. Their tests only look at one region of each page, so this writes an 800-px band of each, full width,
around the Locations, plus the same Locations re-expressed as 0-1 of the band. The boxes are carried over by arithmetic
from tests/fixtures/{erob80,cubemars-ak80-9.twin}.locations.json (the ones measured on the whole page), not re-measured.
DIR holds eRob80.html.jpg and ak80-9-v3-0-robotic-actuator.html.jpg (make_fixtures.py's snap folder) or, as
scripts/build_demo.py names them, erob80.jpg and cubemars-ak80-9.jpg.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
BAND = 800  # px of page kept, centred on the Locations

PAGES = [
    # (snapshot names, the whole-page Locations, the band written, its Locations)
    (("eRob80.html.jpg", "erob80.jpg"), "erob80.locations.json", "erob80-band.jpg", "erob80-band.locations.json"),
    (("ak80-9-v3-0-robotic-actuator.html.jpg", "cubemars-ak80-9.jpg"), "cubemars-ak80-9.twin.locations.json",
     "cubemars-ak80-9-twin-band.jpg", "cubemars-ak80-9.twin-band.locations.json"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snap", type=Path, default=REPO / "demo" / "assets")
    snap = parser.parse_args().snap
    for names, whole_name, band_name, band_locations_name in PAGES:
        source = next(snap / name for name in names if (snap / name).exists())
        image = Image.open(source).convert("RGB")
        width, height = image.size
        locations = json.loads((FIXTURES / whole_name).read_text(encoding="utf-8"))
        middle = (min(loc["box"][1] for loc in locations) + max(loc["box"][3] for loc in locations)) / 2 * height
        top = max(0, min(height - BAND, round(middle - BAND / 2)))
        image.crop((0, top, width, top + BAND)).save(FIXTURES / band_name, quality=92)
        moved = [
            {**loc, "box": [loc["box"][0], round((loc["box"][1] * height - top) / BAND, 6),
                            loc["box"][2], round((loc["box"][3] * height - top) / BAND, 6)]}
            for loc in locations
        ]
        (FIXTURES / band_locations_name).write_text(json.dumps(moved, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{band_name}: rows {top}-{top + BAND} of {source.name} ({width}x{height}), {len(moved)} Locations")


if __name__ == "__main__":
    main()
