"""Offline unit tests for the autonomous loop's status parsing."""

from dream.core.loop import _parse_status, _parse_verdict


def test_done():
    assert _parse_status("did stuff\nSTATUS: DONE\nNEXT: nothing") == ("DONE", "nothing")


def test_continue():
    status, nxt = _parse_status("working\nSTATUS: CONTINUE\nNEXT: browse the top result")
    assert status == "CONTINUE" and nxt == "browse the top result"


def test_need_input():
    status, nxt = _parse_status("STATUS: NEED_INPUT\nNEXT: Which repo should I use?")
    assert status == "NEED_INPUT" and "repo" in nxt


def test_no_block():
    assert _parse_status("just some text") == (None, "")


def test_last_block_wins():
    text = "STATUS: CONTINUE\nNEXT: a\n...more...\nSTATUS: DONE\nNEXT: b"
    assert _parse_status(text) == ("DONE", "b")


def test_verdict_pass():
    assert _parse_verdict("looks good\nVERDICT: PASS\nGAPS: none") == ("PASS", "none")


def test_verdict_fail_with_gaps():
    verdict, gaps = _parse_verdict("VERDICT: FAIL\nGAPS: criterion 2 unmet; no sources cited")
    assert verdict == "FAIL" and "criterion 2" in gaps


def test_verdict_defaults_fail():
    # No verdict line -> conservative FAIL (evaluator must affirmatively PASS).
    assert _parse_verdict("some text without a verdict")[0] == "FAIL"
