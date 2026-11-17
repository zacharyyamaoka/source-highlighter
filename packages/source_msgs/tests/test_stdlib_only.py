"""source_msgs is the vocabulary every source app imports, so importing it must pull in nothing but the stdlib.

WHY measured, not trusted: a stray `import PIL` would make markdownify and datasheet-extractor install Pillow,
Playwright or each other — the very coupling this package exists to avoid (PEP 0001, criterion C1).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def test_import_and_use_loads_only_the_stdlib():
    program = f"""
import json, sys
sys.path.insert(0, {str(SRC)!r})
before = set(sys.modules)
import source_msgs
from source_msgs import Source, PageBox, parse
Source.from_url("https://example.com/a.pdf").highlight(parse("page=1&xywh=percent:1,1,5,5"))
loaded = {{name.split(".")[0] for name in set(sys.modules) - before}}
print(json.dumps(sorted(loaded - set(sys.stdlib_module_names) - {{"source_msgs"}})))
"""
    # -I: isolated mode, so neither the cwd nor site-packages can lend it a module.
    output = subprocess.run([sys.executable, "-I", "-c", program], capture_output=True, text=True, check=True).stdout
    assert json.loads(output) == []
