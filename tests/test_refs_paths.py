"""A file:// URL is percent-encoded; a bare absolute path is not. WHY (ObjectDB, 2026-11-05): ref vitacore-ppe-22,
file:///home/bam/zach_brain/Sources/Apple%20Notes/Policy%20(APPLE%20NOTE).md, was reported missing while the file
existed, because %20 reached the filesystem undecoded."""

from pathlib import Path

from source_highlighter.refs import local_path


def test_a_file_url_is_percent_decoded(tmp_path: Path):
    note = tmp_path / "Apple Notes" / "Policy (APPLE NOTE).md"
    note.parent.mkdir()
    note.write_text("x")
    url = "file://" + str(note).replace(" ", "%20")
    assert local_path(url) == note and local_path(url).is_file()


def test_a_file_url_with_a_host_of_localhost_is_the_same_file(tmp_path: Path):
    note = tmp_path / "a b.md"
    note.write_text("x")
    assert local_path("file://localhost" + str(note).replace(" ", "%20")) == note


def test_a_bare_absolute_path_is_taken_literally(tmp_path: Path):
    literal = tmp_path / "100%20percent.md"  # a real '%' in a file name stays as written
    literal.write_text("x")
    assert local_path(str(literal)) == literal
