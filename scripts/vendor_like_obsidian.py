#!/usr/bin/env python3
"""Vendor Zach's Obsidian-parity markdown viewer into js/like-obsidian/, pinned by commit and sha256:

    python3 /home/bam/source-highlighter/scripts/vendor_like_obsidian.py --ref main

The rendered-markdown view (`{kind: "markdown"}`) draws a note through this viewer's `mountNote`. Modelled on Vibe Tracks'
tools/vendor_like_obsidian.py and like-obsidian-vscode's tools/vendor_viewer.py: built from a `git archive` of the commit
(`vite build --config embed.vite.config.js`), so the copy is that commit's alone, never a checkout's uncommitted edits — the
viewer's main checkout carries someone else's work in progress, and this script never touches it (no checkout, stash or
worktree). VENDOR.json names the commit; manifest.json holds every file's sha256; tests/test_markdown_rendered.py holds the
copy to it. WHY vendored whole: the viewer is replaced whole, never patched here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
TARGET = ROOT / "js" / "like-obsidian"
DEFAULT_REPO = pathlib.Path("/home/bam/like_obsidian_md_viewer")
UPSTREAM = "https://github.com/kenforthewin/atomic-editor"


def git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=pathlib.Path, default=DEFAULT_REPO)
    parser.add_argument("--ref", default="main", help="a commit, branch or tag of the viewer repo (default: main)")
    args = parser.parse_args()
    commit = git(args.repo, "rev-parse", f"{args.ref}^{{commit}}")
    with tempfile.TemporaryDirectory(prefix="like-obsidian-vendor-") as work_name:
        work = pathlib.Path(work_name)
        archive = subprocess.run(["git", "-C", str(args.repo), "archive", "--format=tar", commit], check=True,
                                 capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", str(work)], input=archive, check=True)
        # WHY the commit's own lockfile: the checkout's node_modules belong to whatever branch it sits on.
        subprocess.run(["npm", "ci", "--no-audit", "--no-fund", "--ignore-scripts", "--prefer-offline"], cwd=work, check=True,
                       stdout=subprocess.DEVNULL)
        subprocess.run(["npx", "vite", "build", "--config", "embed.vite.config.js"], cwd=work, check=True,
                       stdout=subprocess.DEVNULL)
        dist = work / "dist" / "embed"
        files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dist.iterdir()) if p.is_file()}
        if TARGET.exists():
            shutil.rmtree(TARGET)
        TARGET.mkdir(parents=True)
        for name in files:
            shutil.copyfile(dist / name, TARGET / name)
        shutil.copyfile(work / "LICENSE", TARGET / "LICENSE")
    manifest = json.dumps({"files": files}, indent=1, sort_keys=True) + "\n"
    (TARGET / "manifest.json").write_text(manifest, encoding="utf-8")
    (TARGET / "VENDOR.json").write_text(json.dumps({
        "project": "like_obsidian_md_viewer (Zach's Obsidian-parity markdown viewer)",
        "ref": args.ref, "commit": commit, "subject": git(args.repo, "log", "-1", "--format=%s", commit),
        "built_with": "vite build --config embed.vite.config.js on a git archive of the commit",
        "manifest_sha256": hashlib.sha256(manifest.encode()).hexdigest(),
        "files": len(files), "license": f"MIT (fork of {UPSTREAM}; see LICENSE)"}, indent=1) + "\n", encoding="utf-8")
    print(f"vendored {commit[:7]} ({len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
