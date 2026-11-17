"""Quote matching folds lookalike code points (NFKC + CJK radical variants) and maps back to the original characters."""

from __future__ import annotations

import re

from source_highlighter.check import _squash, radical_variants

from .conftest import REPO

LOOKALIKE = "传动⽐ 12"  # ⽐ is U+2F50 KANGXI RADICAL COMPARE; a reader types 比


def js_variants() -> dict[str, str]:
    source = (REPO / "js" / "source-highlighter.js").read_text(encoding="utf-8")
    pair = re.search(r'RADICAL_VARIANTS = new Map\(\[\.\.\."([^"]+)"\]\.map\(.*?\[\.\.\."([^"]+)"\]\[index\]', source)
    return dict(zip(pair.group(1), pair.group(2)))


def test_the_js_radical_table_is_the_one_derived_from_unicode_names():
    assert js_variants() == radical_variants() and len(radical_variants()) == 32


def test_squash_folds_lookalikes_and_whitespace_but_leaves_ordinary_text():
    assert _squash(LOOKALIKE) == _squash("传动比12") == "传动比12"
    assert _squash("⺼ ⽐") == "肉比"  # a supplement variant and a Kangxi radical
    assert _squash("Rated torque") == "Ratedtorque"


def test_the_quote_find_matches_lookalikes_and_marks_the_original_characters(harness):
    found = harness.evaluate(
        """async ([text, quote]) => {
            const { quoteCandidates } = window.sourceHighlighter;
            return quoteCandidates(text, quote, null);
        }""",
        [f"ab {LOOKALIKE} cd", "传动比 12"],
    )
    assert len(found["exact"]) == 1
    text = f"ab {LOOKALIKE} cd"
    start, end = found["exact"][0]["start"], found["exact"][0]["end"]
    assert text[start:end] == LOOKALIKE  # the original characters, not the folded ones


def test_a_cluster_that_folds_to_several_characters_still_ends_on_its_own_boundary(harness):
    text = "x㎏y"  # U+338F SQUARE KG folds to "kg"
    found = harness.evaluate("([text, quote]) => window.sourceHighlighter.quoteCandidates(text, quote, null)", [text, "kg"])
    assert [(m["start"], m["end"]) for m in found["exact"]] == [(1, 2)]
