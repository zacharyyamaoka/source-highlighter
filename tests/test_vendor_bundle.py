"""scripts/vendor.py: the folder an app drops in. Built from a commit (git archive) or, for these tests, the working
tree; every file pinned by sha256 in VENDOR.json; `--check` catches a changed, missing or extra file; it never
replaces a folder it did not write."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest

from .conftest import REPO

VENDOR = REPO / "scripts" / "vendor.py"
EXPECTED = {
    "source-highlighter.js", "source-msgs.js", "open.html", "open.js", "open.css", "lens.html", "lens.js", "viewer.css", "common.css",
    "peek.js", "peek.css", "shared.js", "LICENSE", "vendor/approx-string-match.js", "vendor/match-quote.js",
    "vendor/LICENSE.approx-string-match", "vendor/LICENSE.hypothesis", "vendor/VENDOR.json",
    "pdfjs/pdf.min.mjs", "pdfjs/pdf.worker.min.mjs", "pdfjs/LICENSE",
}


def vendor(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(VENDOR), *args], capture_output=True, text=True)


@pytest.fixture
def bundle(tmp_path):
    if not (REPO / "js" / "node_modules" / "pdfjs-dist").exists():
        pytest.skip("PDF.js not installed: run `pnpm --dir js install`")
    into = tmp_path / "sh"
    result = vendor("--worktree", "--into", str(into))
    assert result.returncode == 0, result.stderr
    return into


def test_the_bundle_is_the_pinned_files_and_nothing_else(bundle):
    record = json.loads((bundle / "VENDOR.json").read_text(encoding="utf-8"))
    assert set(record["files"]) == EXPECTED
    on_disk = {path.relative_to(bundle).as_posix() for path in bundle.rglob("*") if path.is_file()} - {"VENDOR.json"}
    assert on_disk == EXPECTED
    for name, digest in record["files"].items():
        assert hashlib.sha256((bundle / name).read_bytes()).hexdigest() == digest
    assert (bundle / "source-highlighter.js").read_bytes() == (REPO / "js" / "source-highlighter.js").read_bytes()
    assert record["license"] == "MIT (LICENSE)" and "MIT License" in (bundle / "LICENSE").read_text()
    assert record["commit"] == subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert "pdfjs-dist 6.4.299, Apache-2.0" in record["third_party"]["pdfjs/"]
    assert record["entry"].startswith("open.html?src=")
    assert "/home/" not in (bundle / "VENDOR.json").read_text(encoding="utf-8")  # a published folder names no machine path
    assert vendor("--check", str(bundle)).returncode == 0


def test_check_catches_a_changed_missing_or_extra_file(bundle):
    (bundle / "open.js").write_text("// edited in the app\n", encoding="utf-8")
    (bundle / "peek.css").unlink()
    (bundle / "notes.txt").write_text("x", encoding="utf-8")
    result = vendor("--check", str(bundle))
    assert result.returncode == 1
    assert "changed open.js" in result.stderr and "missing peek.css" in result.stderr and "extra notes.txt" in result.stderr


def test_it_replaces_its_own_folder_whole_and_refuses_anyone_elses(bundle, tmp_path):
    (bundle / "stale.js").write_text("x", encoding="utf-8")
    assert vendor("--worktree", "--into", str(bundle)).returncode == 0
    assert not (bundle / "stale.js").exists()  # replaced whole, never patched
    other = tmp_path / "someone-elses"
    other.mkdir()
    (other / "index.html").write_text("mine", encoding="utf-8")
    result = vendor("--worktree", "--into", str(other))
    assert result.returncode != 0 and "refusing to replace it" in result.stderr
    assert (other / "index.html").read_text() == "mine"


def test_without_pdfjs_leaves_it_out_and_says_how_to_point_at_yours(tmp_path):
    into = tmp_path / "sh"
    assert vendor("--worktree", "--without-pdfjs", "--into", str(into)).returncode == 0
    record = json.loads((into / "VENDOR.json").read_text(encoding="utf-8"))
    assert not (into / "pdfjs").exists() and not any(name.startswith("pdfjs/") for name in record["files"])
    assert "pdfjs=" in record["third_party"]["pdfjs/"]


def test_a_commit_is_vendored_from_git_archive(tmp_path):
    """The default: a commit's own files, never a checkout's uncommitted edits."""
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    shown = subprocess.run(["git", "-C", str(REPO), "show", "HEAD:viewer/open.html"], capture_output=True)
    if shown.returncode != 0:
        pytest.skip("HEAD predates the static viewer")
    into = tmp_path / "sh"
    result = vendor("--ref", "HEAD", "--into", str(into))
    assert result.returncode == 0, result.stderr
    record = json.loads((into / "VENDOR.json").read_text(encoding="utf-8"))
    assert record["commit"] == head and record["built_from"] == f"git archive of {head[:12]}" and record["dirty"] is False
    assert (into / "open.html").read_bytes() == shown.stdout
