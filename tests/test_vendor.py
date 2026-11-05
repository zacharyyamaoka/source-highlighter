"""Vendored JS stays exactly what js/vendor/VENDOR.json records (origin, version, sha256)."""

from __future__ import annotations

import hashlib
import json

from .conftest import REPO

VENDOR = REPO / "js" / "vendor"


def test_every_vendored_file_matches_its_record():
    record = json.loads((VENDOR / "VENDOR.json").read_text(encoding="utf-8"))["files"]
    on_disk = {path.name for path in VENDOR.iterdir() if path.name != "VENDOR.json"}
    assert on_disk == set(record)
    for name, entry in record.items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == entry["sha256"], name
        assert entry["origin"] and entry["version"]


def test_the_matcher_is_loaded_relative_to_the_module():
    module = (REPO / "js" / "source-highlighter.js").read_text(encoding="utf-8")
    assert 'new URL("./vendor/match-quote.js", import.meta.url)' in module
    assert "from './approx-string-match.js'" in (VENDOR / "match-quote.js").read_text(encoding="utf-8")
