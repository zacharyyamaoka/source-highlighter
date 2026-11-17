"""Source: every constructor gives the same frozen type, and it round-trips to CONTRACT §5 JSON."""

from __future__ import annotations

import dataclasses
import hashlib
import http.server
import json
import threading
from pathlib import Path

import pytest
from source_msgs import Source
from source_msgs._source import KINDS

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
PDF = FIXTURES / "j60-joint.pdf"
HTML = FIXTURES / "cubemars-ak80-9.html"
MARKDOWN = FIXTURES / "cubemars-ak80-9-spec.md"


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_MSGS_CACHE", str(tmp_path / "cache"))


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("path", "kind", "media_type"),
    [(PDF, "pdf", "application/pdf"), (HTML, "html", "text/html"), (MARKDOWN, "text", "text/markdown")],
)
def test_from_file_sets_every_field(path, kind, media_type):
    source = Source.from_file(path)
    assert source.kind == kind
    assert source.media_type == media_type
    assert source.sha256 == sha256_of(path)
    assert source.id == source.sha256
    assert source.path == str(path.resolve())
    assert source.origin == str(path.resolve())
    assert source.retrieved_at.endswith("Z")
    assert source.title == path.stem
    assert source.size == path.stat().st_size
    assert source.read_bytes() == path.read_bytes()


def test_from_file_sniffs_bytes_not_the_extension(tmp_path):
    blob = tmp_path / "blob-without-extension"
    blob.write_bytes(PDF.read_bytes())
    assert Source.from_file(blob).kind == "pdf"


def test_image_kind(tmp_path):
    png = tmp_path / "scan.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    source = Source.from_file(png)
    assert (source.kind, source.media_type) == ("image", "image/png")


def test_source_is_frozen_and_hashable():
    source = Source.from_file(PDF)
    with pytest.raises(dataclasses.FrozenInstanceError):
        source.kind = "html"
    with pytest.raises(TypeError):
        source.stored["pdf"] = "/elsewhere"
    assert hash(source) == hash(Source.from_dict(source.to_dict()))


def test_from_text_is_content_addressed(tmp_path):
    first = Source.from_text("# Spec\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
    second = Source.from_text("# Spec\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
    assert first.sha256 == second.sha256
    assert (first.kind, first.media_type) == ("text", "text/markdown")
    assert Path(first.path).is_relative_to(tmp_path / "cache")
    assert first.text().startswith("# Spec")
    plain = Source.from_text("hello", media_type="text/plain")
    assert Path(plain.path).suffix == ".txt"


def test_from_url_is_link_only_and_does_no_network():
    source = Source.from_url("https://www.steadywin.cn/en/h-pd-9.html")
    assert (source.kind, source.media_type) == ("html", "text/html")
    assert source.sha256 is None and source.path is None and source.retrieved_at is None
    assert source.id == hashlib.sha256(b"https://www.steadywin.cn/en/h-pd-9.html").hexdigest()
    assert not source.is_saved
    assert Source.from_url("https://example.com/datasheet.PDF").kind == "pdf"
    with pytest.raises(ValueError, match="no saved bytes"):
        source.read_bytes()


def test_from_url_refuses_non_http():
    with pytest.raises(ValueError, match="http"):
        Source.from_url("ftp://example.com/x.pdf")


@pytest.mark.parametrize("bad", [{"kind": "video"}, {"sha256": "abc"}, {"retrieved_at": "yesterday"}])
def test_bad_fields_are_refused(bad):
    fields = {"origin": "https://example.com", "kind": "html", "media_type": "text/html", **bad}
    with pytest.raises(ValueError):
        Source(**fields)


def test_sha256_without_a_path_is_refused():
    with pytest.raises(ValueError, match="path"):
        Source(origin="https://example.com", kind="html", media_type="text/html", sha256="0" * 64)


def test_to_dict_is_a_contract_5_source_and_round_trips():
    source = Source.from_file(HTML)
    data = source.to_dict()
    assert data["url"] == source.origin
    assert data["stored"]["html"] == source.path  # CONTRACT §5: the stored html copy check/view fall back on
    assert json.loads(json.dumps(data)) == data
    assert Source.from_dict(data) == source


def test_from_dict_reads_a_contract_5_reference_source():
    reference_source = {
        "url": "https://www.cubemars.com/product/ak80-9-v3-0-robotic-actuator.html",
        "retrieved_at": "2026-10-29T19:44:27Z",
        "sha256": sha256_of(HTML),
        "media_type": "text/html",
        "stored": {"html": str(HTML), "snapshot": "/abs/snapshot.jpg"},
    }
    source = Source.from_dict(reference_source)
    assert source.kind == "html"
    assert source.origin == reference_source["url"]
    assert source.path == str(HTML)
    assert source.stored["snapshot"] == "/abs/snapshot.jpg"


def test_from_bundle_reads_a_markdownify_capture_folder(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "source.html").write_bytes(HTML.read_bytes())
    (bundle / "snapshot.jpg").write_bytes(b"\xff\xd8\xff" + b"\0" * 16)
    (bundle / "source.json").write_text(json.dumps({
        "sha256": sha256_of(HTML), "bytes": HTML.stat().st_size, "media_type": "text/html",
        "retrievals": [{"url": "https://maker.example/p.html", "final_url": "https://maker.example/p.html",
                        "retrieved_at": "2026-11-16T20:00:00Z"}],
    }))
    source = Source.from_bundle(bundle)
    assert source.origin == "https://maker.example/p.html"
    assert source.retrieved_at == "2026-11-16T20:00:00Z"
    assert source.sha256 == sha256_of(HTML)
    assert source.path == str(bundle / "source.html")
    assert source.stored["snapshot"] == str(bundle / "snapshot.jpg")


def test_every_constructor_gives_the_same_type():
    made = [Source.from_file(PDF), Source.from_text("x"), Source.from_url("https://example.com/"),
            Source.from_dict(Source.from_file(HTML).to_dict())]
    assert {type(source) for source in made} == {Source}
    assert all(source.kind in KINDS for source in made)


class _Pages(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/robots.txt":
            body, kind = b"User-agent: *\nDisallow: /private\n", "text/plain"
        elif self.path == "/spec.html":
            body, kind = HTML.read_bytes(), "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_capture_without_markdownify_fetches_politely_and_saves(site):
    link = Source.from_url(f"{site}/spec.html")
    captured = link.capture(using="stdlib")
    assert captured.is_saved
    assert captured.origin == link.origin
    assert captured.sha256 == sha256_of(HTML)
    assert Path(captured.path).read_bytes() == HTML.read_bytes()
    assert captured.kind == "html" and captured.retrieved_at
    assert captured.capture() is captured  # already saved: a no-op


def test_capture_honours_robots(site):
    with pytest.raises(PermissionError, match="robots"):
        Source.from_url(f"{site}/private/page.html").capture(using="stdlib")


def test_capture_of_a_local_path_reads_the_file():
    captured = Source.from_url(str(PDF)).capture()
    expected = Source.from_file(PDF)
    assert (captured.sha256, captured.path, captured.kind, captured.title) == (expected.sha256, expected.path, "pdf", "j60-joint")


def test_to_markdown_of_markdown_and_text():
    assert Source.from_file(MARKDOWN).to_markdown() == MARKDOWN.read_text(encoding="utf-8")
    assert Source.from_text("plain words", media_type="text/plain").to_markdown() == "plain words"


def test_to_markdown_of_a_pdf_without_markdownify_names_the_extra(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_markdownify(name, *args, **kwargs):
        if name == "markdownify" or name.startswith("markdownify."):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_markdownify)
    with pytest.raises(ImportError, match=r"source-msgs\[markdown\]"):
        Source.from_file(PDF).to_markdown()
