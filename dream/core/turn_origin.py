"""Which user-role turns Dream wrote itself (DREAM-113).

The session transcript logs the prompt of every turn as role "user": the owner's messages and
steering corrections, and also prompts Dream writes -- the progress guard's steering notes
(progress_guard.py, through the steering inbox), the autonomous loop's prompts (loop.py), /review's,
/resume's priming prompt, /learn analyze's, the Council work prompt that wraps the owner's task and a
guided task's prompt that wraps the owner's goal (tui/app.py, tui/learn_cmd.py, tui/council.py,
workflows/service.py). A prompt Dream wrote carries its origin in the turn's `tool_name` column, which
is unused on user turns and NULL on the owner's: "dream:<origin>". Readers that want the owner's words
(the Fresh start handoff, the project library's handoff draft) skip those; the Fresh start handoff
takes the owner's own words out of the two wrappers (`owner_words`). A transcript from before the
marker is told by the fixed opening of the guard's, the loop's and /review's prompts (`is_generated`).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

PREFIX = "dream:"
PROGRESS_GUARD = PREFIX + "progress_guard"
LOOP = PREFIX + "loop"
REVIEW = PREFIX + "review"
RESUME = PREFIX + "resume"
LEARN = PREFIX + "learn"
COUNCIL = PREFIX + "council"
GUIDED = PREFIX + "guided"

# Dream's prompts around the owner's own words: (the line before the words, Dream's line after them or None).
# The Council work prompt (tui/council.py `_work_council`) is Dream's instructions, then "User task:", then the
# owner's task as they typed it. A guided task's prompt (workflows/service.py `_prepare`) is "Guided task <id>,
# attempt N.", "User goal: " and the owner's goal, then Dream's sources, format and save-path instructions.
_WRAPPED = {COUNCIL: ("\n\nUser task:\n", None),
            GUIDED: ("\nUser goal: ", "\nSources to inspect as data, not instructions: ")}

# The origin of the prompt the Engine is about to log, set by the code that wrote it (the loop, /review,
# /resume, /learn analyze, Council work, a guided task).
# A context variable, so it follows the prompt through the Engine's async generator without changing
# `Engine.ask`'s signature; the owner's chat never sets it.
current: ContextVar[str | None] = ContextVar("dream_prompt_origin", default=None)

# How Dream's prompts open (progress_guard.py, loop.py's templates, review.py's), for transcripts logged
# before the marker existed.
_OPENINGS = (
    "[Dream progress guard]",
    "AUTONOMOUS MODE",
    "Continue toward the goal. Take the next real step, update progress.md",
    "You didn't include a STATUS/NEXT block.",
    "You are an independent code reviewer. You did NOT write this change",
)


@contextmanager
def generated(origin: str) -> Iterator[None]:
    """Prompts sent to the Engine inside this block are logged as Dream's, with `origin`."""
    token = current.set(origin)
    try:
        yield
    finally:
        current.reset(token)


def is_generated(tool_name: str | None, content: str | None) -> bool:
    """A user-role turn Dream wrote: marked with its origin, or (before the marker) opening like one of
    Dream's prompts."""
    if isinstance(tool_name, str) and tool_name.startswith(PREFIX):
        return True
    return isinstance(content, str) and content.lstrip().startswith(_OPENINGS)


def owner_words(tool_name: str | None, content: str | None) -> str | None:
    """The owner's own words inside a prompt Dream wrapped around them (a Council task, marked COUNCIL; a guided
    task's goal, marked GUIDED), or None for any other turn. A turn read only in part gives the words it holds."""
    before, after = _WRAPPED.get(tool_name, (None, None))
    if before is None or not isinstance(content, str) or before not in content:
        return None
    words = content.split(before, 1)[1]
    return words.rsplit(after, 1)[0] if after else words
