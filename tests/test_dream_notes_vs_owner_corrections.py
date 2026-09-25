"""DREAM-131 (fix list #108, #110).

A note Dream writes mid-turn (the progress guard) reaches the history under the owner's steering name,
`dream_steering_user`, marked as Dream's only by DREAM-127's form: no [id:mNNNN] tag and a `[Dream` opening. The
filer, the verifier's request scope and `snip`'s protection of the owner's words read the name alone, so they took
Dream's note for an owner correction. The owner's corrections are read exactly as before.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from dream.core import turn_origin
from dream.core.backends.openai_compat import OpenAICompatBackend

GUARD = "[Dream progress guard] 12 consecutive read-only steps."
CORRECTION = "make the wheels chrome"


def _backend():
    p = SimpleNamespace(key="machx", label="MachX", base_url="http://x/v1", multimodal=False, api_key=lambda: "n")
    b = OpenAICompatBackend(provider=p, model="m", system_prompt="SYSTEM PROMPT", tools=[], permission_cb=None)
    b.n_ctx = None
    return b


class _Inbox:
    def __init__(self, receipts):
        self.receipts = receipts

    async def drain(self, *, final=False):
        out, self.receipts = self.receipts, []
        return out


def _active_turn(*receipts):
    """An active turn as `_ask` opens it, then the steering receipts as `_apply_steering` appends them."""
    b = _backend()
    b._msg_seq = 1
    b.messages.extend([{"role": "user", "name": "dream_active_user", "content": "Build the car page.\n\n[id:m0001]"},
                       {"role": "assistant", "content": "Page built."}])
    b.steering_inbox = _Inbox(list(receipts))
    asyncio.run(b._apply_steering())
    return b


def _scope(b):
    text = b._verification_prompt("page.html")
    return json.loads(text[text.index("{"):text.rindex("}") + 1])


def test_the_filer_does_not_file_dreams_note_as_an_owner_correction():
    b = _active_turn({"id": "a" * 32, "text": GUARD, "origin": turn_origin.PROGRESS_GUARD},
                     {"id": "b" * 32, "text": CORRECTION})
    filed = b._turn_text("Build the car page.")
    assert "Build the car page." in filed and CORRECTION in filed
    assert "progress guard" not in filed


def test_the_verifier_scope_holds_the_owners_words_not_dreams_note():
    b = _active_turn({"id": "b" * 32, "text": CORRECTION},
                     {"id": "a" * 32, "text": GUARD, "origin": turn_origin.PROGRESS_GUARD})
    scope = _scope(b)
    assert [r.split("\n\n[id:")[0] for r in scope["active_turn_requests_in_order"]] == ["Build the car page.",
                                                                                         CORRECTION]
    # The note is the last user message; the owner's latest words are still the current request.
    assert scope["current_user_request"] == CORRECTION + "\n\n[id:m0002]"


def test_an_owner_correction_that_opens_like_dreams_still_counts():
    # The owner may type "[Dream]" too; their correction carries an id tag, which Dream's notes never do.
    b = _active_turn({"id": "b" * 32, "text": "[Dream] is the page title"})
    assert "[Dream] is the page title\n\n[id:m0002]" in _scope(b)["active_turn_requests_in_order"]
    assert "[Dream] is the page title" in b._turn_text("Build the car page.")


def _history(middle):
    b = _backend()
    b.messages.extend([{"role": "user", "content": "first\n\n[id:m0001]"}, {"role": "assistant", "content": "a1"},
                       middle, {"role": "assistant", "content": "a2"},
                       {"role": "user", "content": "second\n\n[id:m0002]"}, {"role": "assistant", "content": "a3"},
                       {"role": "user", "content": "third\n\n[id:m0003]"}])
    return b


def test_dreams_note_does_not_pin_a_snipped_span():
    b = _history({"role": "user", "name": "dream_steering_user", "content": GUARD})
    b._snips = [("m0001", "m0001", "done")]
    assert b._execute_snips() == 4
    assert [m.get("content") for m in b.messages[1:]] == ["second\n\n[id:m0002]", "a3", "third\n\n[id:m0003]"]


def test_an_owner_correction_still_pins_its_span():
    # ADR-024: the owner's required words survive snipping. A correction carries its own id, so a span holds one
    # when it starts or ends on it.
    b = _history({"role": "user", "name": "dream_steering_user", "content": CORRECTION + "\n\n[id:m0004]"})
    b._snips = [("m0001", "m0004", "done")]
    assert b._execute_snips() == 0
    assert len(b.messages) == 8
