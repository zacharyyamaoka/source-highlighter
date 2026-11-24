"""js/source-msgs.js is source_msgs' compact selectors in the browser (open.html reads `where=` with it). The Python
package is the oracle: on one corpus — every selector kind, the fixtures' own Locations, robot_models-style anchors,
escapes, non-BMP text and malformed strings — both must read the same selector, make the same CONTRACT §2 Location
for every source kind (or refuse it with the same reason), and write the same compact string."""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import fields

import pytest
from source_msgs import PageBox, TextQuote, Unconvertible, from_location, parse
from source_msgs.selectors import Css, Note, Page, TableCell, TextPosition, XPath

from .conftest import FIXTURES

MARKDOWN = (FIXTURES / "cubemars-ak80-9-spec.md").read_text(encoding="utf-8")
KINDS = ("pdf", "image", "html", "text")


def corpus() -> list[str]:
    selectors = [
        TextQuote("20 Nm", prefix="Rated torque ", suffix=" Peak"),
        TextQuote("Mixed paper 23.4%", page=78),
        TextQuote("a, b & c-d = e"),
        TextQuote("额定扭矩(Nominal Torque)"),
        TextQuote("𝜏 = 3.82 N·m"),  # a character outside the BMP: code points, not UTF-16 units
        TextQuote("Rated torque (Nm)", label="Rated torque", id="kt"),
        TextPosition(840, 846),
        PageBox(2, 0.61, 0.40, 0.07, 0.02),
        PageBox(1, 0.183, 0.763, 0.063, 0.026, sha256="a" * 64),
        PageBox(1, 0.0, 0.0, 1.0, 0.5, label="top half"),
        Page(33),
        Page(2, sha256="b" * 64),
        XPath("/html/body/main/table[2]/tr[3]/td[2]"),
        Css("table.spec td:nth-child(2)"),
        TableCell(3, 2, table=2),
        TableCell(1, 1),
        Note(heading="Pricing", block="ab12cd"),
        Note(heading="Specs & limits"),
    ]
    strings = [selector.compact() for selector in selectors]
    # the fixtures' own Locations, as their callers store them
    for name in ("j60-joint.locations.json", "erob80-band.locations.json", "cubemars-ak80-9-spec.md.locations.json"):
        for location in json.loads((FIXTURES / name).read_text(encoding="utf-8")):
            try:
                strings.append(from_location(location).compact())
            except ValueError:
                pass  # an html Location with several selectors has no single compact string
    # robot_models Stated.where / .anchor as written in Zach's part files
    strings += [
        "page=1&xywh=percent:18.3,76.3,6.3,2.6",
        "table=1&row=7&col=3",
        "text=" + urllib.parse.quote("堵转扭矩(Stall Torque)", safe=""),
        "text=" + urllib.parse.quote("Rated torque, peak-to-peak", safe=""),
        "#page=2&text=350.4W",
        "  page=4  ",
        # gfm table cells of the markdown fixture, and quotes pinned by their context in it
        "table=1&row=2&col=2",
        "table=1&row=99&col=1",
        "table=9&row=1&col=1",
        "text=Rated%20torque%20(Nm)-,9,-",
        # malformed: both must refuse
        "",
        "page=0",
        "page=two",
        "page=2&page=3",
        "nonsense",
        "page=2&xywh=pixel:1,2,3,4",
        "page=2&xywh=percent:1.2.3,4,5,6",
        "page=1&xywh=percent:90,90,20,20",
        "char=5,2",
        "text=a,b,c",
        "text=x&sha256=" + "c" * 64,
        "page=2&sha256=NOTHEX",
        "table=0&row=1&col=1",
        "heading=&block=",
        "colour=red",
    ]
    return strings


def python_side(text: str) -> dict:
    try:
        selector = parse(text)
    except ValueError:
        return {"error": True}
    out = {
        "type": type(selector).__name__,
        "fields": {item.name: getattr(selector, item.name) for item in fields(selector) if getattr(selector, item.name) is not None},
        "compact": selector.compact(),
        "locations": {},
    }
    for kind in KINDS:
        try:
            out["locations"][kind] = selector.to_location(kind, MARKDOWN if kind == "text" else None)
        except Unconvertible as error:
            out["locations"][kind] = {"unconvertible": str(error)}
    return out


JS_SIDE = """async ([strings, markdown, kinds]) => {
    const msgs = await import("/js/source-msgs.js");
    return strings.map((text) => {
        let selector;
        try { selector = msgs.parse(text); } catch (error) { return {error: true}; }
        const {type, ...rest} = selector;
        const fieldsOut = {};
        for (const [key, value] of Object.entries(rest)) if (value !== undefined && value !== null) fieldsOut[key] = value;
        const locations = {};
        for (const kind of kinds) {
            try { locations[kind] = msgs.toLocation(selector, kind, kind === "text" ? markdown : null); }
            catch (error) {
                if (error.name !== "Unconvertible") throw error;
                locations[kind] = {unconvertible: error.message};
            }
        }
        return {type, fields: fieldsOut, compact: msgs.compact(selector), locations};
    });
}"""


def normalize(value):
    """Floats compared to nine places (both sides round a box to nine); empty prefix/suffix as absent."""
    if isinstance(value, float):
        return round(value, 9)
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items() if not (key in ("prefix", "suffix") and item == "")}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return value


def test_the_js_port_reads_writes_and_converts_like_source_msgs(harness):
    strings = corpus()
    expected = [python_side(text) for text in strings]
    actual = harness.evaluate(JS_SIDE, [strings, MARKDOWN, list(KINDS)])
    assert len(actual) == len(expected)
    mismatches = [(text, want, got) for text, want, got in zip(strings, expected, actual) if normalize(want) != normalize(got)]
    assert not mismatches, "\n".join(f"{text!r}:\n  python {want}\n  js     {got}" for text, want, got in mismatches[:5])
    readable = sum(1 for entry in expected if not entry.get("error"))
    refused = len(expected) - readable
    print(f"source-msgs parity: {len(strings)} strings, {readable} read alike, {refused} refused alike")
    assert readable >= 30 and refused >= 12
    assert harness.errors == []


def test_quote_value_matches_python_percent_encoding(harness):
    samples = ["Rated torque (Nm)", "a, b & c-d = e", "堵转扭矩", "x+y/z#w?", "100%", "𝜏 · N·m", "~_.-!*'()"]
    from source_msgs.selectors import _quote

    got = harness.evaluate(
        """async (samples) => { const msgs = await import("/js/source-msgs.js");
            return samples.map((s) => [msgs.quoteValue(s), msgs.unquoteValue(msgs.quoteValue(s))]); }""",
        samples,
    )
    assert [pair[0] for pair in got] == [_quote(sample) for sample in samples]
    assert [pair[1] for pair in got] == samples


@pytest.mark.parametrize("given", ["page=2&xywh=percent:61,40,7,2", {"page": 2, "box": [0.61, 0.4, 0.68, 0.42]}])
def test_as_location_takes_a_compact_string_or_a_contract_dict(harness, given):
    got = harness.evaluate(
        """async (given) => { const msgs = await import("/js/source-msgs.js"); return msgs.asLocation(given, "pdf"); }""",
        given,
    )
    assert normalize(got) == normalize({"page": 2, "box": [0.61, 0.4, 0.68, 0.42]})
