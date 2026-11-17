"""Turn pytest's JUnit XML into site/tests.json, the landing page's test section (Zach, 2026-11-16: "on the landing page
of source highlighter please show the tests and whether they are passing or not").

    python3 scripts/ci_tests_json.py junit.xml site/tests.json

Run by .github/workflows/pages.yml after `pytest --junitxml`; the commit, run and runner come from the Actions
environment. WHY from the XML, never a list kept by hand: the page must show what actually ran, on that commit.
Stdlib only.
"""

from __future__ import annotations

import ast
import json
import os
import platform
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ACCEPTANCE = "test_acceptance"


def outcome(case: ET.Element) -> tuple[str, str]:
    """pytest's JUnit: <failure>, <error>, <skipped type="pytest.xfail"> for an xfail, <skipped> for a skip."""
    for tag in ("failure", "error"):
        node = case.find(tag)
        if node is not None:
            return ("failed" if tag == "failure" else "error"), (node.get("message") or "").strip()
    node = case.find("skipped")
    if node is not None:
        kind = "xfailed" if node.get("type") == "pytest.xfail" else "skipped"
        return kind, (node.get("message") or "").strip()
    return "passed", ""


def docstrings(module: Path) -> tuple[str, dict[str, str]]:
    """The module's docstring's first paragraph and each test's own docstring or the `# ---- n. …` section it sits under."""
    tree = ast.parse(module.read_text(encoding="utf-8"))
    lines = module.read_text(encoding="utf-8").splitlines()
    sections = [(index + 1, re.sub(r"^# -+ *|-+$", "", line).strip()) for index, line in enumerate(lines) if line.startswith("# ----")]
    found = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            doc = ast.get_docstring(node)
            section = next((title for number, title in reversed(sections) if number < node.lineno), "")
            found[node.name] = " ".join(doc.split()) if doc else section
    module_doc = (ast.get_docstring(tree) or "").split("\n\n")[0]
    return " ".join(module_doc.split()), found


def main() -> int:
    junit, out = Path(sys.argv[1]), Path(sys.argv[2])
    root = ET.parse(junit).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    cases = []
    for case in root.iter("testcase"):
        kind, message = outcome(case)
        classname = case.get("classname") or ""
        module = classname.split(".")[-1]
        name = case.get("name") or ""
        function, _, params = name.partition("[")
        cases.append({"file": classname.replace(".", "/") + ".py", "module": module, "function": function, "params": params.rstrip("]"),
                      "outcome": kind, "message": message.splitlines()[0][:300] if message else "", "seconds": float(case.get("time") or 0)})
    totals = Counter(case["outcome"] for case in cases)
    files = defaultdict(Counter)
    for case in cases:
        files[case["file"]][case["outcome"]] += 1
    acceptance_doc, docs = docstrings(REPO / "tests" / f"{ACCEPTANCE}.py")
    grouped: dict[str, list[dict]] = defaultdict(list)
    for case in cases:
        if case["module"] == ACCEPTANCE:
            grouped[case["function"]].append(case)
    acceptance = []
    for function in docs:  # in the file's order
        runs = grouped.get(function, [])
        counts = Counter(case["outcome"] for case in runs)
        status = next((word for word in ("error", "failed", "xpassed", "skipped", "xfailed") if counts[word]), "passed") if runs else "missing"
        acceptance.append({"name": function, "doc": docs[function], "status": status, "counts": dict(counts),
                           "cases": [{"params": case["params"], "outcome": case["outcome"], "message": case["message"]} for case in runs]})
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repository = os.environ.get("GITHUB_REPOSITORY", "zacharyyamaoka/source-highlighter")
    commit = os.environ.get("GITHUB_SHA", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    report = {
        "totals": dict(totals),
        "seconds": sum(float(suite.get("time") or 0) for suite in suites),
        "commit": commit,
        "commit_url": f"{server}/{repository}/commit/{commit}",
        "run_number": os.environ.get("GITHUB_RUN_NUMBER", "local"),
        "run_url": f"{server}/{repository}/actions/runs/{run_id}" if run_id else "",
        "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "runner": f"{os.environ.get('RUNNER_OS', platform.system())} {os.environ.get('ImageOS', '')}, Python {platform.python_version()}".replace("  ", " "),
        "acceptance_doc": acceptance_doc,
        "acceptance": acceptance,
        "files": [{"file": file, **counts} for file, counts in sorted(files.items())],
    }
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report["totals"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
