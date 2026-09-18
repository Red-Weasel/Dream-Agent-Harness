"""The artifact panel's security invariants.

The panel renders HTML and SVG that a MODEL wrote, inside the same page that
holds the session token in its URL and a composer wired to a shell-capable
agent. That makes the iframe the sharpest edge in the GUI, and its safety rests
on a handful of properties that are easy to regress by accident and silent when
they do. So they are asserted against the shipped asset itself.

The one that matters most: `allow-scripts` together with `allow-same-origin`
defeats the sandbox completely — the frame would share this origin, reach the
parent DOM, and read the token out of location.search. Apart, the frame gets a
unique opaque origin and can do neither.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

UI = Path(__file__).parent.parent / "dream" / "gui" / "static" / "index.html"


@pytest.fixture(scope="module")
def ui() -> str:
    return UI.read_text(encoding="utf-8")


def test_the_ui_asset_ships(ui):
    assert len(ui) > 5000


# --- the sandbox --------------------------------------------------------------


def test_artifact_frames_are_sandboxed(ui):
    assert "setAttribute('sandbox', 'allow-scripts')" in ui


def test_allow_same_origin_is_never_granted_anywhere(ui):
    """The combination that would defeat the sandbox. Not present at all — not
    in a string, not in an attribute, not in a comment describing what to do."""
    for line in ui.splitlines():
        if "allow-same-origin" in line:
            # Only a comment explaining why it is absent may mention it.
            assert line.lstrip().startswith(("//", "*", "/*", "-")), (
                f"allow-same-origin appears in live code: {line.strip()}"
            )


def test_no_escape_hatches_are_granted(ui):
    dangers = ("allow-top-navigation", "allow-popups", "allow-modals",
               "allow-forms", "allow-downloads", "allow-pointer-lock")
    for line in ui.splitlines():
        if line.lstrip().startswith(("//", "*", "/*", "-")):
            continue  # a comment naming what is withheld is not a grant
        for danger in dangers:
            assert danger not in line, (
                f"{danger} must not be granted to artifact frames: {line.strip()}"
            )


def test_frames_are_built_from_srcdoc_not_a_fetched_url(ui):
    """srcdoc means nothing is fetched to construct the frame."""
    assert "f.srcdoc = doc" in ui
    assert not re.search(r"\bf\.src\s*=", ui)


# --- the content security policy ---------------------------------------------


def test_a_csp_is_injected_into_every_rendered_artifact(ui):
    assert "ART_CSP" in ui
    csp = re.search(r"const ART_CSP\s*=\s*(.+?);\n", ui, re.S)
    assert csp, "ART_CSP must be defined"
    policy = csp.group(1)
    assert "default-src 'none'" in policy  # deny by default
    assert "connect-src" not in policy     # never opened back up


def test_the_csp_denies_network_destinations(ui):
    """A generated page must not be able to phone home with what it can see."""
    csp = re.search(r"const ART_CSP\s*=\s*(.+?);\n", ui, re.S).group(1)
    # Only data:/blob: sources are permitted, and only for images and fonts.
    assert "img-src data: blob:" in csp
    assert "http" not in csp and "//" not in csp.replace("'", "")


def test_the_csp_is_applied_to_both_svg_and_html_artifacts(ui):
    body = ui[ui.index("function paintArtifact"):]
    head = body[:body.index("$('artver').onchange")]
    # one meta tag built from ART_CSP, and it is the FIRST thing in the srcdoc for
    # every kind of body — svg, full document, fragment. Gate 8: splicing it in at
    # the first "<head"/"<html" match let a page with '<head>' in a script string
    # push the meta into the body, where Chromium ignores it.
    assert 'content="${ART_CSP}"' in head
    assert "`<!doctype html>${csp}${ART_BRIDGE}" in head
    assert ".replace(/<head" not in head and ".replace(/<html" not in head


def test_referrer_is_suppressed(ui):
    assert "referrerpolicy" in ui and "no-referrer" in ui


# --- model text is never trusted as markup -----------------------------------


def test_chips_escape_model_supplied_titles(ui):
    """An artifact's title comes from a filename the model chose."""
    assert "esc(a.title)" in ui


def test_the_code_view_escapes_rather_than_renders(ui):
    assert "esc(body)" in ui


def test_markdown_escapes_before_it_marks_up(ui):
    """The renderer must escape FIRST, then apply markup to escaped text."""
    fn = ui[ui.index("function md(src)"):]
    fn = fn[:fn.index("\n}")]
    assert "esc(src)" in fn
    assert fn.index("esc(src)") < fn.index("replace(/```")


# --- behaviour the panel promises --------------------------------------------


def test_identical_re_emission_does_not_create_a_new_version(ui):
    assert "a.versions[a.versions.length-1] === body" in ui


def test_only_completed_messages_are_scanned(ui):
    """A half-streamed fence is not an artifact yet."""
    assert "scanForArtifacts(current.raw, current.seq)" in ui
    stream_case = ui[ui.index("case 'text_delta'"):ui.index("case 'thinking_delta'")]
    assert "scanForArtifacts" not in stream_case


def test_artifact_detection_covers_written_files_and_fenced_blocks(ui):
    assert "function artifactFromWrite" in ui and "function scanForArtifacts" in ui
    assert re.search(r"write_file\|Write\|Edit\|MultiEdit", ui)


def test_detection_failures_cannot_break_the_stream(ui):
    """A malformed artifact must not take the transcript down with it."""
    for call in ("scanForArtifacts(current.raw, current.seq)", "artifactFromWrite(d.input || {})"):
        i = ui.index(call)
        assert "try{" in ui[max(0, i - 90):i]
