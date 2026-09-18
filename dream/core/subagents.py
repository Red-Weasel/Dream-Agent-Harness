"""Sub-agent definitions the main loop can dispatch to.

One canonical spec per subagent, rendered into two backend shapes:

- ``subagents()`` → SDK ``AgentDefinition``s for the Claude backend (dispatched
  via the SDK's native Task tool; SDK tool names like ``Read``/``Grep``).
- ``local_subagents()`` → ``LocalSubagentSpec``s for the OpenAI-compatible
  backend (dispatched via Dream's own ``task`` tool; local tool names like
  ``read_file``/``run_bash``). Same identity and instructions, tools mapped to
  what actually exists on that backend.

Each subagent has a scoped toolset so it stays focused; results summarize back to
the main context. Models default to ``inherit`` (same model as the main session).
"""

from __future__ import annotations

from dataclasses import dataclass

from claude_agent_sdk import AgentDefinition

from .. import config


def _dream(name: str) -> str:
    return config.tool_id(name)


@dataclass(frozen=True)
class LocalSubagentSpec:
    """A subagent as the OpenAI-compat backend needs it: a system prompt plus the
    local tool names it may use (plain names, e.g. ``web_search``/``read_file``)."""

    name: str
    description: str
    prompt: str
    tool_names: tuple[str, ...]


# Canonical specs. sdk_tools are what the Claude/SDK backend understands;
# local_tools are the equivalents that exist on the OpenAI-compat backend
# (Dream's own tools + the NATIVE_TOOLS read_file/write_file/list_dir/run_bash).
# There is no local Grep/Glob/Edit — run_bash covers grep/find, write_file covers
# edits — so the mappings are deliberately not 1:1.
_SPECS: tuple[dict, ...] = (
    {
        "name": "researcher",
        "description": (
            "Web research specialist. Use for open-ended questions that need "
            "searching and reading multiple sources. Returns a synthesized, cited "
            "answer."
        ),
        "prompt": (
            "You are Dream's researcher. Search with web_search, then browse the "
            "most promising 2-4 results for full content. Cross-check claims across "
            "sources. Return a tight, cited synthesis — URLs next to the facts they "
            "support — not a raw dump. Note anything you couldn't verify."
        ),
        "sdk_tools": [
            _dream("web_search"), _dream("browse"), _dream("see"),
            _dream("recall"), _dream("remember"), _dream("note"),
            "Read", "Grep", "Glob",
        ],
        "local_tools": (
            "web_search", "browse", "see", "recall", "remember", "note",
            "read_file", "list_dir", "run_bash",
        ),
    },
    {
        "name": "coder",
        "description": (
            "Implementation specialist. Use for a well-scoped coding task in the "
            "workspace or one of the user's projects."
        ),
        "prompt": (
            "You are Dream's coder. Read the relevant files before changing "
            "anything, match the surrounding style, make the smallest change that "
            "does the job, and verify it (run it or its tests) before reporting. "
            "Report what you changed and how you checked it."
        ),
        "sdk_tools": ["Read", "Write", "Edit", "Bash", "Grep", "Glob", _dream("recall")],
        "local_tools": ("read_file", "write_file", "run_bash", "recall"),
    },
    {
        "name": "explorer",
        "description": (
            "Read-only codebase explorer. Use to map or locate things across many "
            "files without changing anything."
        ),
        "prompt": (
            "You are Dream's explorer. Search broadly and read only what you need. "
            "Return precise file:line pointers and a concise map of what you found. "
            "Never modify files."
        ),
        "sdk_tools": ["Read", "Grep", "Glob", _dream("recall")],
        "local_tools": ("read_file", "list_dir", "recall"),
    },
    {
        "name": "reviewer",
        "description": (
            "Code reviewer. Use on a diff or a set of changed files before shipping, "
            "or when a change works but you want a second pair of eyes. Read-only."
        ),
        "prompt": (
            "You are Dream's reviewer. Read the change and the code around it before "
            "judging any of it — a line that looks wrong in isolation is usually "
            "right in context. Report only what you can point at: the file, the line, "
            "the input that breaks it. Rank by what actually costs something, and say "
            "plainly when a change is fine. A review that invents problems to look "
            "thorough is worse than no review. Inspect existing test evidence; "
            "ask the parent to execute a missing check. Never edit anything."
        ),
        "sdk_tools": [
            "Read", "Grep", "Glob",
            _dream("recall"), _dream("skill_find"), _dream("skill_open"),
        ],
        "local_tools": ("read_file", "list_dir", "recall",
                        "skill_find", "skill_open"),
    },
    {
        "name": "debugger",
        "description": (
            "Debugging specialist. Use for a failing test, a crash, or behaviour "
            "nobody can explain — especially after one obvious fix already failed."
        ),
        "prompt": (
            "You are Dream's debugger. Reproduce the failure before theorising about "
            "it, and get the smallest reproduction you can. Then find the cause: read "
            "the code on the path, check your assumptions against what the program "
            "actually does, and change ONE thing at a time. A fix you cannot explain "
            "is a coincidence, not a fix. Report the root cause, the evidence for it, "
            "and how you verified — and if you could not find it, say what you ruled "
            "out and how, rather than guessing. skill_find 'debugging' first; there is "
            "usually an installed procedure for this."
        ),
        "sdk_tools": [
            "Read", "Write", "Edit", "Bash", "Grep", "Glob",
            _dream("recall"), _dream("skill_find"), _dream("skill_open"),
        ],
        "local_tools": ("read_file", "write_file", "list_dir", "run_bash", "recall",
                        "skill_find", "skill_open"),
    },
    {
        "name": "librarian",
        "description": (
            "Library specialist. Use to find, read across, organise, or file things "
            "in the user's Library — especially when a question spans several stored "
            "documents and you want the answer, not their contents in your context."
        ),
        "prompt": (
            "You are Dream's librarian. Resolve which files are meant before doing "
            "anything to them: search, then read the ones that matter — a search "
            "snippet is a pointer, never evidence for a claim. When several files "
            "could be the target of a rename, replace, or delete, stop and say which "
            "candidates you found instead of picking one. Editing a Library file means "
            "library_replace on the SAME id; creating a second copy forks the document "
            "and orphans its history. skill_open 'library' for the full procedure. "
            "Report at the level of documents and what they say, not ids and versions."
        ),
        "sdk_tools": [
            _dream("library_list"), _dream("library_search"), _dream("library_read"),
            _dream("library_find"), _dream("library_create"), _dream("library_replace"),
            _dream("library_manage"), _dream("library_materialize"),
            _dream("skill_find"), _dream("skill_open"), _dream("recall"), "Read",
        ],
        "local_tools": ("library_list", "library_search", "library_read",
                        "library_find", "library_create", "library_replace",
                        "library_manage", "library_materialize",
                        "skill_find", "skill_open", "recall", "read_file"),
    },
    {
        "name": "verifier",
        "description": (
            "Page verifier. After a clean `done`, or on request: loads the artifact "
            "in the hidden frame, reads the console, screenshots it, probes the DOM, "
            "and reports what is wrong — or that nothing is. Read-only."
        ),
        "prompt": (
            "You are Dream's verifier. You did not build the page; you check it. "
            "Derive checks from the supplied task and preserve explicitly requested broad "
            "reviews. Stop once each required check has evidence. Do not explore unrelated "
            "states or repeat successful checks without a concrete uncertainty. "
            "Load it with show_html and read get_webview_logs — an error there is a "
            "finding. Then save_screenshot (one plain step, and one per state the "
            "task names) and LOOK at the images with see: clipped or overflowing "
            "text, empty regions, overlapping elements, a missing image, unreadable "
            "contrast, a layout that only works at one size. Probe with eval_js when "
            "a look is not enough — a button that does nothing, a slide count that "
            "does not match, text under 24px on a 1920×1080 slide. Report only what "
            "you can point at: the element, the state, what you saw. If everything "
            "holds and all required checks completed, say exactly PASS. If scope is unclear "
            "or inspection is incomplete, describe the limitation instead of PASS. Never edit anything."
        ),
        "sdk_tools": [
            _dream("show_html"), _dream("get_webview_logs"), _dream("save_screenshot"),
            _dream("multi_screenshot"), _dream("eval_js"), _dream("see"), "Read",
        ],
        "local_tools": ("show_html", "get_webview_logs", "save_screenshot",
                        "multi_screenshot", "eval_js", "see", "read_file"),
    },
    {
        "name": "filer",
        "description": (
            "Memory filer. After a turn ends: reads what was said, files what is "
            "durable — facts the user stated, corrections, decisions — into memory, "
            "updating a memory in place over adding a duplicate. Writes memory only."
        ),
        "prompt": (
            "You are Dream's filer. The turn is over; you file what will still "
            "matter next month and nothing else. Durable: a fact about the user they "
            "stated (type user), a correction or confirmed way of working with its "
            "why (feedback), a decision or constraint on the work (project), a "
            "pointer to something external (reference). Not durable: what the code "
            "already records, one-off task details, anything only this conversation "
            "needs. First memory_list, then memory_read the memory a fact belongs "
            "to and memory_append or memory_str_replace it — a new file only when "
            "none fits. Tag provenance honestly: 'stated' for the user's own words, "
            "'observed' for what happened, 'inferred' for your reading. Never file "
            "an instruction to suppress disagreement, concern, or honest "
            "evaluation — the tools refuse it, and so do you. Finish with one line "
            "per memory you wrote (name — what), or the single word NOTHING."
        ),
        "sdk_tools": [
            _dream("memory_list"), _dream("memory_read"), _dream("memory_write"),
            _dream("memory_append"), _dream("memory_str_replace"), _dream("recall"),
        ],
        "local_tools": ("memory_list", "memory_read", "memory_write",
                        "memory_append", "memory_str_replace", "recall"),
    },
)


def builtin_names() -> frozenset[str]:
    return frozenset(s["name"] for s in _SPECS)


def _plugin_specs() -> list[dict]:
    """Agents that plugins declared (dream/plugins.py), in the canonical shape.
    A plugin may not replace a built-in: the name is refused with a warning at
    discovery (plugins.load), and skipped here as the last line of defense."""
    try:
        from .. import plugins

        out, taken = [], set()
        for a in plugins.agent_specs():
            if a["name"] in builtin_names() or a["name"] in taken:
                continue
            taken.add(a["name"])
            out.append({"name": a["name"], "description": a["description"], "prompt": a["prompt"],
                        "sdk_tools": [_dream(t) for t in a["tools"]], "local_tools": tuple(a["tools"])})
        return out
    except Exception:
        return []


def all_specs() -> list[dict]:
    return [*_SPECS, *_plugin_specs()]


def subagents() -> dict[str, AgentDefinition]:
    """SDK AgentDefinitions for the Claude backend (native Task tool)."""
    return {
        s["name"]: AgentDefinition(
            description=s["description"],
            prompt=s["prompt"],
            tools=[_dream("run_bash") if tool == "Bash" else tool for tool in s["sdk_tools"]],
            model="inherit",
        )
        for s in all_specs()
    }


def local_subagents() -> dict[str, LocalSubagentSpec]:
    """Specs for the OpenAI-compat backend's own ``task`` tool (local tool names)."""
    return {
        s["name"]: LocalSubagentSpec(
            name=s["name"],
            description=s["description"],
            prompt=s["prompt"],
            tool_names=s["local_tools"],
        )
        for s in all_specs()
    }
