"""Field finding: the launcher parsed ctx/GPU input with a bare `isdigit()`, so
'222,000' — a comma, exactly how a human types a big number — was silently
dropped: no --ctx flag, the engine booted at its 8192 default, no warning, and
the boot banner just omitted the ctx segment. Nothing downstream can catch it
either (MachX has no /props and never logs its loaded context), so the typed
value must be parsed generously and any unparseable input flagged loudly.
"""

from __future__ import annotations

import pytest

from dream.local.launcher import parse_count


@pytest.mark.parametrize("raw,expected", [
    ("222000", 222_000),
    ("222,000", 222_000),   # the trap that started this
    ("222_000", 222_000),
    ("64 000", 64_000),
    ("2", 2),
    ("", None),             # blank = caller's default, no warning
    ("abc", None),          # unparseable = caller warns
    ("12.5", None),
    ("-4", None),
])
def test_parse_count(raw, expected):
    assert parse_count(raw) == expected
