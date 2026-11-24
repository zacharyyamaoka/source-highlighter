"""An archived page stays quiet when its tags run attributes together: `height='62'width='120' src='//…'` (no space after
a quoted value) is a parse error every browser forgives, and SteadyWin's own product page (the GIM4305-10 source in
robotparts' user/parts) writes its logo that way. Before 2026-11-24 the rewrite skipped such a tag, so its src stayed
live: the CSP still blocked the fetch, but the console logged it. Found opening that page in open.html."""

from __future__ import annotations

from .conftest import draw

RUN_TOGETHER = (
    "<!doctype html><html><body><p>Peak torque <b>3.82</b> N·m</p>"
    "<img id='logo' height='62'width='120' src='//example.invalid/logo.png'/>"
    '<a href="#"class="link"><img alt="x"src="//example.invalid/b.png"style="background:url(//example.invalid/c.png)"></a>'
    "<img id='plain' src='//example.invalid/d.png'>"
    "</body></html>"
)


def test_run_together_attributes_are_quieted_too(harness):
    drawn = draw(harness, {"kind": "html", "html": RUN_TOGETHER}, [{"id": "v", "quote": "3.82"}], {"name": "highlight"})
    assert drawn["result"]["unresolved"] == []
    harness.wait_for_timeout(300)
    left = harness.evaluate(
        """() => { const d = document.querySelector("iframe").contentDocument;
                   return [...d.querySelectorAll("img")].map((img) => [img.getAttribute("src"), img.getAttribute("height"), img.getAttribute("style")]); }"""
    )
    assert [src for src, _, _ in left] == [None, None, None]  # every src disabled, the boxes kept
    assert left[0][1] == "62"  # attributes that fetch nothing are left as written
    assert "url(" not in (left[1][2] or "")
    assert not [line for line in harness.console if "Content Security Policy" in line], harness.console
