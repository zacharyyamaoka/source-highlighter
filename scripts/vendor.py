#!/usr/bin/env python3
"""Vendor source-highlighter's static viewer into an app, pinned by commit and sha256 (CONTRACT §7):

    python3 /home/bam/source-highlighter/scripts/vendor.py --ref main --into /path/to/app/static/source-highlighter
    python3 /home/bam/source-highlighter/scripts/vendor.py --check /path/to/app/static/source-highlighter

One flat folder a static site serves as is, no build step and no server: `open.html` (open a stored source at a
Location from a plain URL), the module it draws with (`source-highlighter.js`, `source-msgs.js`, `vendor/`), Lens and
Peek (`lens.html`, `peek.js`, …, which read a refs.json beside them), pdf.js (`pdfjs/`, unless `--without-pdfjs`:
then pass `pdfjs=<your pdf.min.mjs>` in the link) and `LICENSE` (MIT). `VENDOR.json` names the commit and every
file's sha256; `--check` re-hashes a vendored folder against it (exit 1 on any difference).

WHY from a `git archive` of the commit, never the checkout: a main checkout may carry someone's uncommitted work, so
the copy is that commit's alone (as data-ui's `vendor` and scripts/vendor_like_obsidian.py do). `--worktree` copies
the working tree instead, for this repo's own tests; its VENDOR.json says so. WHY replaced whole: a vendored copy is
never edited in the app; fix it here and vendor again. Stdlib only, so an app runs it with plain python3.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
REMOTE = "https://github.com/zacharyyamaoka/source-highlighter"
WHAT = "source-highlighter's static viewer as this app serves it: re-vendor with scripts/vendor.py, never edit"

# (path in the repo, path in the vendored folder). WHY flat: the viewer pages import ./source-highlighter.js, exactly
# where `source-highlighter view` serves it, so the same files work served by the view server or by any static host.
BUNDLE = [
    ("js/source-highlighter.js", "source-highlighter.js"),
    ("js/source-msgs.js", "source-msgs.js"),
    ("js/vendor/approx-string-match.js", "vendor/approx-string-match.js"),
    ("js/vendor/match-quote.js", "vendor/match-quote.js"),
    ("js/vendor/LICENSE.approx-string-match", "vendor/LICENSE.approx-string-match"),
    ("js/vendor/LICENSE.hypothesis", "vendor/LICENSE.hypothesis"),
    ("js/vendor/VENDOR.json", "vendor/VENDOR.json"),
    ("viewer/open.html", "open.html"),
    ("viewer/open.js", "open.js"),
    ("viewer/open.css", "open.css"),
    ("viewer/lens.html", "lens.html"),
    ("viewer/lens.js", "lens.js"),
    ("viewer/viewer.css", "viewer.css"),
    ("viewer/common.css", "common.css"),
    ("viewer/peek.js", "peek.js"),
    ("viewer/peek.css", "peek.css"),
    ("viewer/shared.js", "shared.js"),
    ("LICENSE", "LICENSE"),
]
PDFJS_FILES = [("build/pdf.min.mjs", "pdfjs/pdf.min.mjs"), ("build/pdf.worker.min.mjs", "pdfjs/pdf.worker.min.mjs"), ("LICENSE", "pdfjs/LICENSE")]


def git(repo: pathlib.Path, *args: str, binary: bool = False):
    result = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return result.stdout if binary else result.stdout.decode().strip()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pdfjs_pin(lockfile: str) -> tuple[str, str]:
    """The pdfjs-dist version and tarball integrity the commit's js/pnpm-lock.yaml pins."""
    match = re.search(r"^  pdfjs-dist@([\w.\-]+):\n    resolution: \{integrity: (sha512-[A-Za-z0-9+/=]+)\}", lockfile, re.M)
    if not match:
        raise SystemExit("vendor: js/pnpm-lock.yaml pins no pdfjs-dist; run `pnpm --dir js install` and commit the lockfile")
    return match.group(1), match.group(2)


def pdfjs_files(repo: pathlib.Path, version: str, integrity: str) -> tuple[dict[str, bytes], str]:
    """pdf.js's files at the pinned version: the checkout's install when it is that version, else the npm tarball,
    checked against the lockfile's integrity."""
    local = repo / "js" / "node_modules" / "pdfjs-dist"
    if (local / "package.json").is_file() and json.loads((local / "package.json").read_text())["version"] == version:
        return ({dest: (local / src).read_bytes() for src, dest in PDFJS_FILES},
                f"js/node_modules/pdfjs-dist (pnpm install of the lockfile's pdfjs-dist@{version}, tarball {integrity[:23]}…)")
    url = f"https://registry.npmjs.org/pdfjs-dist/-/pdfjs-dist-{version}.tgz"
    data = urllib.request.urlopen(url, timeout=120).read()  # noqa: S310 - a fixed https URL
    actual = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
    if actual != integrity:
        raise SystemExit(f"vendor: {url} does not match the lockfile's integrity ({actual[:30]}… vs {integrity[:30]}…)")
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        files = {dest: archive.extractfile(f"package/{src}").read() for src, dest in PDFJS_FILES}
    return files, f"{url} (integrity {integrity[:23]}…, matches the lockfile)"


def collect(repo: pathlib.Path, ref: str | None, worktree: bool, with_markdown: bool) -> tuple[dict[str, bytes], dict]:
    """Every file of the bundle (vendored path → bytes) and the provenance VENDOR.json records."""
    commit = git(repo, "rev-parse", f"{ref or 'HEAD'}^{{commit}}")
    pairs = list(BUNDLE)
    files: dict[str, bytes] = {}
    if worktree:
        for src, dest in pairs:
            files[dest] = (repo / src).read_bytes()
        if with_markdown:
            for path in sorted((repo / "js" / "like-obsidian").iterdir()):
                files[f"like-obsidian/{path.name}"] = path.read_bytes()
        lockfile = (repo / "js" / "pnpm-lock.yaml").read_text(encoding="utf-8")
        dirty = bool(git(repo, "status", "--porcelain", "--", *[src for src, _ in pairs]))
        built = "the working tree of " + str(repo) + (" (with uncommitted changes)" if dirty else " (clean)")
    else:
        with tempfile.TemporaryDirectory(prefix="source-highlighter-vendor-") as work_name:
            work = pathlib.Path(work_name)
            archive = git(repo, "archive", "--format=tar", commit, binary=True)
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                tar.extractall(work, filter="data")
            missing = [src for src, _ in pairs if not (work / src).is_file()]
            if missing:
                raise SystemExit(f"vendor: commit {commit[:7]} has no {', '.join(missing)} (older than the static viewer?)")
            for src, dest in pairs:
                files[dest] = (work / src).read_bytes()
            if with_markdown:
                for path in sorted((work / "js" / "like-obsidian").iterdir()):
                    files[f"like-obsidian/{path.name}"] = path.read_bytes()
            lockfile = (work / "js" / "pnpm-lock.yaml").read_text(encoding="utf-8")
        dirty = False
        built = f"git archive of {commit[:12]}"
    record = {
        "what": WHAT,
        "repo": str(repo),
        "remote": REMOTE,
        "ref": ref or ("HEAD" if worktree else "main"),
        "commit": commit,
        "commit_date": git(repo, "log", "-1", "--format=%cI", commit),
        "subject": git(repo, "log", "-1", "--format=%s", commit),
        "built_from": built,
        "dirty": dirty,
        "entry": "open.html?src=<stored copy>&where=<source_msgs selector>[&quote=…&label=…&url=…&retrieved=…] (CONTRACT §7)",
        "license": "MIT (LICENSE)",
    }
    return files, {"record": record, "lockfile": lockfile}


def write(into: pathlib.Path, files: dict[str, bytes], record: dict) -> None:
    if into.exists():
        old = into / "VENDOR.json"
        if not old.is_file() or json.loads(old.read_text(encoding="utf-8")).get("what") != WHAT:
            raise SystemExit(f"vendor: {into} exists and is not a source-highlighter vendor folder; refusing to replace it")
        shutil.rmtree(into)
    for name, data in files.items():
        target = into / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    manifest = {name: sha256(data) for name, data in sorted(files.items())}
    record["files"] = manifest
    record["manifest_sha256"] = sha256(json.dumps(manifest, sort_keys=True).encode())
    (into / "VENDOR.json").write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def check(folder: pathlib.Path) -> int:
    """Re-hash a vendored folder against its VENDOR.json: every file there, nothing else, nothing changed."""
    record = json.loads((folder / "VENDOR.json").read_text(encoding="utf-8"))
    expected = record["files"]
    present = {path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file()} - {"VENDOR.json"}
    problems = []
    for name, digest in expected.items():
        path = folder / name
        if not path.is_file():
            problems.append(f"missing {name}")
        elif sha256(path.read_bytes()) != digest:
            problems.append(f"changed {name}")
    problems += [f"extra {name}" for name in sorted(present - set(expected))]
    if sha256(json.dumps(expected, sort_keys=True).encode()) != record.get("manifest_sha256"):
        problems.append("VENDOR.json's manifest_sha256 does not match its file list")
    for line in problems:
        print(f"vendor --check: {line}", file=sys.stderr)
    print(f"{folder}: {'OK' if not problems else 'DIFFERS'} · {len(expected)} files · {record['commit'][:12]} ({record['subject']})")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--into", type=pathlib.Path, help="the folder to (re)write, e.g. site/static/source-highlighter")
    parser.add_argument("--ref", help="a commit, branch or tag of this repo (default: main)")
    parser.add_argument("--repo", type=pathlib.Path, default=ROOT, help="the source-highlighter checkout (default: this one)")
    parser.add_argument("--worktree", action="store_true", help="copy the working tree instead of a commit (this repo's tests)")
    parser.add_argument("--without-pdfjs", action="store_true", help="leave pdf.js out; links then pass pdfjs=<your pdf.min.mjs>")
    parser.add_argument("--with-markdown", action="store_true", help="add the rendered-markdown viewer (like-obsidian/, ~2 MB, 50+ files)")
    parser.add_argument("--check", type=pathlib.Path, metavar="FOLDER", help="re-hash a vendored folder against its VENDOR.json")
    args = parser.parse_args(argv)
    if args.check:
        return check(args.check)
    if not args.into:
        parser.error("--into is required (or --check FOLDER)")
    if args.worktree and args.ref:
        parser.error("--worktree copies the working tree; --ref names a commit — pick one")
    repo = args.repo.resolve()
    files, meta = collect(repo, args.ref if not args.worktree else None, args.worktree, args.with_markdown)
    record = meta["record"]
    third_party = {
        "vendor/approx-string-match.js": "approx-string-match 2.0.1, MIT (vendor/LICENSE.approx-string-match)",
        "vendor/match-quote.js": "Hypothesis client match-quote, BSD-2-Clause (vendor/LICENSE.hypothesis)",
    }
    if not args.without_pdfjs:
        version, integrity = pdfjs_pin(meta["lockfile"])
        pdfjs, origin = pdfjs_files(repo, version, integrity)
        files.update(pdfjs)
        third_party["pdfjs/"] = f"pdfjs-dist {version}, Apache-2.0 (pdfjs/LICENSE); from {origin}"
    else:
        third_party["pdfjs/"] = "left out (--without-pdfjs): pass pdfjs=<URL of pdf.min.mjs> in open.html links"
    if args.with_markdown:
        third_party["like-obsidian/"] = "like_obsidian_md_viewer, MIT (like-obsidian/LICENSE; see like-obsidian/VENDOR.json)"
    record["third_party"] = third_party
    into = args.into.resolve()
    write(into, files, record)
    print(f"vendored source-highlighter {record['commit'][:7]} ({len(files)} files) into {into}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
