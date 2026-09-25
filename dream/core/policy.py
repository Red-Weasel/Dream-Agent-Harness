"""Workspace-scoped permission policy — where the mode cycle meets the boundary.

Dream points at one workspace per session (a repo, usually). Reads are free
everywhere: Dream may look around the machine. Writes obey the mode *inside*
the workspace — and one rule survives every mode: WRITES OUTSIDE THE WORKSPACE
REQUIRE HUMAN APPROVAL. Auto mode does not override it. The TUI can remember an
explicit native Bash exact-command approval for the current session/scope. That rule
covers shell too: a command that writes or deletes outside the workspace asks
in every mode, since ``cwd`` does not confine an absolute path or a redirect.

Every tool is classified by *capability* so the mode cycle is the single
authority for both backends:
- ``READONLY`` — looks but doesn't touch (Read/Grep/recall/web_search/browse…).
  Free in every mode.
- ``MEMORY`` — Dream editing its own mind (remember/note/forget). Free in every
  mode: plan mode restrains the outside world, not Dream's own cognition, and
  consolidation depends on these never prompting.
- ``WRITE`` — file writes/edits. Obey the mode inside the workspace; ask outside.
- ``SHELL`` — bash. Obey the mode, but escalate to ask when the command targets
  outside the workspace.
- ``MUTATING`` — everything else, *including every self-built custom tool*
  (arbitrary Python). Requires approval unless its executor supplies a separately
  enforced contract. A Bash sandbox does not contain an unrelated MCP tool.
"""

from __future__ import annotations

import os
import fnmatch
import re
import shlex
from collections.abc import Iterable
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .execution import ExecutionScope, SandboxCapability

MODES = ("ask", "accept-edits", "auto", "plan")
MODE_LABEL = {
    "ask": "asking each change",
    "accept-edits": "accept edits on",
    "auto": "auto mode on",
    "plan": "plan mode on",
}

# Capability classes.
READONLY = "readonly"
MEMORY = "memory"
WRITE = "write"
SHELL = "shell"
MUTATING = "mutating"
DESTRUCTIVE = "destructive"
# DREAM-109: arbitrary code whose only executor is an enforced sandbox (live Blender's).
CONTAINED = "contained"

# Capabilities that are free in every mode (never gated).
AUTO_CAPS = frozenset({READONLY, MEMORY})

# SDK builtins + Dream natives, by what they can do to the world.
_READ_ONLY = {
    "Read", "Glob", "Grep", "LS", "NotebookRead", "TodoWrite",
    "read_file", "list_dir",
    "media_read",
    # Dream MCP tools that only observe.
    "recall", "recall_sessions", "read_notes", "web_search", "browse", "see", "computer_observe",
    "skill_list", "skill_load",
    "skill_find", "skill_open", "skill_file", "skill_refresh",
    "library_list", "library_search", "library_read", "library_find", "library_resolve",
    "checkpoint_list",
    # Phase 3 file tools that only look (or only wait).
    "grep", "image_metadata", "sleep",
    # DREAM-094: pixel statistics, a diff and OCR of image files -- reads only
    "measure_image",
    # DREAM-097: shows workspace images with a question in Studio (questions_v2's form); asks, changes nothing
    "visual_check",
    # DREAM-113: the workspace's outline (its Understand map, or a scan) -- reads only
    "project_outline",
    # Studio: the hidden frame has no network, and these change nothing on disk
    # (multi_screenshot writes only under Dream's screenshot folder, like browse).
    "show_html", "get_webview_logs", "show_to_user", "done", "eval_js", "multi_screenshot",
    "eval_js_user_view", "screenshot_user_view", "present_fs_item_for_download",
    # a question form changes nothing; it asks
    "questions_v2",
    # GitHub reads go through the user's own `gh` login
    "github_list_repos", "github_get_tree", "github_read_file",
    # Phase 8: a card renders what the model passed and touches nothing; the
    # image search reads the web like web_search; the rules, the quick form,
    # and the research offer only speak.
    "chart_display_v0", "comparison_card_display_v0", "featured_card_display_v0",
    "itinerary_display_v0", "link_preview_display_v0", "options_card_display_v0",
    "places_list_display_v0", "product_carousel_display_v0", "quiz_display_v0",
    "step_card_display_v0", "translation_display_v0", "visualize_read_me",
    "image_search", "ask_user_input", "suggest_research",
    # Phase 9b: looking at memory and past sessions is reading
    "memory_list", "memory_read", "read_session",
    # Phase 11: listing open work is reading
    "task_list",
    # Phase 12: searching and offering plugins and skills changes nothing
    "search_plugins", "search_skills", "suggest_plugin_install", "suggest_skills",
    "demonstration_list", "demonstration_read", "capability_lab_inspect",
    # DREAM-109: live Blender's scene reads, viewport picture, API lookups and window id
    "blender__get_scene_info", "blender__get_object_info", "blender__get_viewport_screenshot",
    "blender__describe_node_type", "blender__bpy_api_lookup", "blender__get_window",
    # DREAM-129: listing the scene snapshots
    "blender__list_scene_snapshots",
}
# DREAM-109: live Blender's code and export tools. They run only inside the live-Blender
# sandbox (dream.media.blender_live: the workspace read-write, no network, no host
# fallback), and the server name "blender" is reserved for it (dream.mcp_client), so these
# names cannot come from any other server.
_CONTAINED = {"blender__execute_blender_code", "blender__export_scene",
              "blender__restore_scene_snapshot"}  # DREAM-129: reopens a scene snapshot
# Dream editing its own long-term memory — internal cognition, always allowed.
_MEMORY = {"remember", "note", "forget", "skill_save", "skill_patch",
           # the plan and the project's name are the session's own mind, not the world
           "update_todos", "update_plan", "project_note", "set_project_title",
           # Phase 9b: the file tools on memory, delete included — like forget
           "memory_write", "memory_append", "memory_str_replace", "memory_delete",
           # Phase 11: the task store is the session's own mind about its work
           "task_add", "task_update",
           # DREAM-108: moving a memory or session between projects is Dream's own memory, like memory_write
           "memory_move"}
_WRITE = {"Write", "Edit", "MultiEdit", "NotebookEdit", "write_file", "library_materialize",
          "str_replace_edit", "copy_files", "save_screenshot", "copy_starter_component",
          "super_inline_html", "gen_pptx", "github_import_files",
          # both write into the workspace (files a script saves; the PDF)
          "run_script", "open_for_print",
          # with no Studio open it writes the widget under the workspace
          "visualize_show_widget", "demonstration_draft", "capability_lab_create"}
_SHELL = {"Bash", "run_bash"}
# Removes things. Explicit destructive capability preserves path checks and asks
# even in Auto, except for the validated, bounded red-team deletion grant.
_DESTRUCTIVE = {"delete_file"}
_DELEGATING = {"Task"}  # a subagent can write; it is not read-only inspection

# Self-built tools, by name, as declared by the registry each boot. Provenance —
# not the name string — decides their capability: a tool Dream wrote for itself is
# arbitrary Python whatever it calls itself, so naming it "Read" must not hand it
# Read's free pass. This also keeps the classifier honest if the lists above grow
# later to include a name some custom tool already uses.
_CUSTOM_NAMES: set[str] = set()

_PATH_KEYS = ("file_path", "path", "notebook_path", "save_path", "output_path")
_RM_RE = re.compile(r"(^|[;&|]\s*)rm\s")

# Shell confinement is decided by FAILING SAFE: a command asks unless it can be
# proven confined to the workspace. An allowlist of "dangerous" verbs is unsound —
# it fails open on every writer not listed (tar, gcc -o, git clone…) and on any
# interpreter payload (bash -c, python -c, $(…)) that hides the real command. So we
# escalate on ANY path that resolves outside, any redirect target outside, any
# command substitution, and any inline interpreter code — reads included. (Dream
# still looks around the machine freely via the Read/read_file tools; only *shell*
# touching outside the workspace asks, because a shell reader trivially becomes a
# writer and static analysis can't reliably tell them apart.)
_INTERPRETERS = {
    "bash", "sh", "dash", "zsh", "ksh", "fish", "python", "python2", "python3",
    "pypy", "perl", "ruby", "node", "nodejs", "php", "lua",
}
_INLINE_CODE_FLAGS = {"-c", "-lc", "-ic", "-lic", "-ec", "-e", "--command"}
# Interpreters whose program is an ordinary OPERAND instead of a flagged payload:
# `awk 'BEGIN{system("…")}'` looks like a plain string argument, so the flag scan
# below never sees it, and the program can system()/pipe to a shell/print into any
# file. There is no flag to key on and no confined form to carve out (`-f prog`
# reads the same opaque program, `-f -` reads it from stdin), so the whole family
# is unconfined — it asks, always.
_PROGRAM_ARG_INTERPRETERS = {"awk", "gawk", "mawk", "nawk"}
_SUBSTITUTION = ("$(", "`", "<(", ">(")
# Unspaced shell operators glue an interpreter onto the previous word — shlex reads
# `cat f|awk 'prog'` as the single token "f|awk", and the scan below never sees the
# interpreter at all. Padding them apart only inserts whitespace, so quoting stays
# balanced and a quoted operator still travels inside its own token.
_OPERATOR_PAD = re.compile(r"([;|&()])")
# Character devices that are safe redirect/argument targets even though they
# resolve outside the workspace.
_DEV_SINKS = {
    "/dev/null", "/dev/zero", "/dev/stdout", "/dev/stderr",
    "/dev/tty", "/dev/full", "/dev/random", "/dev/urandom",
}
# A redirect target anywhere: optional fd/&, > or >>, optional clobber-override |,
# optional space, then the target. Matches `>f`, `>> f`, `&>f`, `>|f`, `2>f`.
_REDIRECT_SCAN = re.compile(r"(?:&|\d)?>>?\|?\s*([^\s;|&<>]+)")


def next_mode(mode: str) -> str:
    return MODES[(MODES.index(mode) + 1) % len(MODES)] if mode in MODES else MODES[0]


def builtin_names() -> frozenset[str]:
    """Every name this classifier recognizes as a built-in identity. The registry
    refuses to register a custom tool that claims one, so a self-built tool can
    never be *mistaken* for the builtin it named itself after."""
    return frozenset(_READ_ONLY | _MEMORY | _WRITE | _SHELL | _DESTRUCTIVE | _DELEGATING | _CONTAINED)


def declare_custom_tools(names: Iterable[str]) -> None:
    """Tell the classifier which tools came out of ``custom/`` this boot. Called by
    the registry before anything asks for a capability."""
    _CUSTOM_NAMES.clear()
    _CUSTOM_NAMES.update(names)


def _short(tool_name: str) -> str:
    """``mcp__<MCP_SERVER_NAME>__<tool>`` -> ``<tool>`` for Dream's OWN server only; every other name stays whole."""
    if tool_name.startswith("mcp__"):
        from .. import config

        parts = tool_name.split("__", 2)
        if len(parts) == 3 and parts[1] == config.MCP_SERVER_NAME:
            return parts[2]
    return tool_name


def capability(tool_name: str) -> str:
    """Classify a tool (SDK builtin, Dream MCP tool, or self-built custom tool)
    by what it can do. Unknown MCP/custom tools default to MUTATING — a self-built
    tool is arbitrary code and gets no free pass just for existing.

    Only Dream's OWN prefix — ``mcp__<MCP_SERVER_NAME>__`` — is stripped, and the
    remainder must match a KNOWN built-in identity exactly. A custom tool named ``evil__recall``
    must not inherit recall's read-only pass by tail-matching on ``__`` — anything
    not exactly a known built-in is MUTATING. And a declared self-built tool is
    MUTATING by provenance before any name is consulted, so wearing a builtin's
    name buys it nothing."""
    # mcp__dream__<tool> → <tool>, and ONLY for Dream's own server. Any other
    # mcp__… name keeps its full form and so can never match a built-in below.
    # Gate 2 finding 1: an external server that called itself "mcp" minted
    # `mcp__read_file`, the old tail-strip handed it read_file's free pass, and
    # the local backend ran it without asking. A custom tool that smuggles
    # more "__" into its name keeps them for the same reason.
    short = _short(tool_name)
    if short in _CUSTOM_NAMES:
        return MUTATING
    if short in _DESTRUCTIVE:
        return DESTRUCTIVE
    if short in _WRITE:
        return WRITE
    if short in _SHELL:
        return SHELL
    if short in _MEMORY:
        return MEMORY
    if short in _READ_ONLY:
        return READONLY
    if short in _CONTAINED:
        return CONTAINED
    return MUTATING


def _within(p: Path, workspace: Path) -> bool:
    try:
        return p == workspace or p.is_relative_to(workspace)
    except Exception:
        return False


def _path_outside(raw: str, workspace: Path) -> bool:
    """Does a shell path token resolve outside the workspace? Expands ~ and env
    vars; an absolute path overrides the workspace join. Flags (leading '-') and
    empties are not paths."""
    raw = raw.strip().strip('"').strip("'")
    if not raw or raw.startswith("-"):
        return False
    expanded = os.path.expandvars(os.path.expanduser(raw))
    try:
        p = (workspace / Path(expanded)).resolve()
    except Exception:
        return True  # unresolvable → treat as suspicious
    return not _within(p, workspace)


def _names_binary(tok: str, family: set[str]) -> bool:
    """True if a token names a binary in ``family`` — tolerant of version suffixes
    (python3.12, ruby3.0, pypy3, gawk5) so the gate can't be dodged by naming the
    versioned binary."""
    name = Path(tok).name
    if name in family:
        return True
    stripped = re.sub(r"[\d.]+$", "", name)  # python3.12 → python, ruby3.0 → ruby
    return bool(stripped) and stripped in family


def _has_interpreter_payload(tokens: list[str]) -> bool:
    """An interpreter running a program we can't read: inline code (bash -c,
    python -c, perl -e, …), code from stdin, or an operand-program interpreter
    (awk and friends). The payload is opaque to static analysis, so we can't prove
    it's confined — escalate. (Running a *script file* is fine; the file itself is
    a path token that the outside-path check handles.)"""
    if any(not t.startswith("-") and _names_binary(t, _PROGRAM_ARG_INTERPRETERS)
           for t in tokens):
        return True
    interp_positions = [i for i, t in enumerate(tokens)
                        if not t.startswith("-") and _names_binary(t, _INTERPRETERS)]
    if not interp_positions:
        return False
    # Inline-code flag — match by PREFIX so a glued payload (python -c'import os…',
    # where shlex yields the single token "-cimport os…") is still caught.
    if any(t.startswith(f) for t in tokens for f in _INLINE_CODE_FLAGS):
        return True
    # A bare interpreter with no script-file argument reads stdin / is interactive
    # (e.g. `echo 'code' | python3`) — equally unconfined. A script file would be a
    # non-flag path token following the interpreter.
    for i in interp_positions:
        if not any(not t.startswith("-") for t in tokens[i + 1:]):
            return True
    return False


def _path_candidates(tok: str) -> list[str]:
    """Path-like fragments in a single shell token: the token itself, and the
    value of any ``key=value`` operand (of=/etc/x, --output=/etc/x)."""
    tok = tok.strip().strip('"').strip("'")
    if not tok:
        return []
    cands: list[str] = []
    if "=" in tok:
        val = tok.split("=", 1)[1]
        if val:
            cands.append(val)
    if not tok.startswith("-"):
        cands.append(tok)
    return cands


def _shell_unconfined(command: str, workspace: Path) -> bool:
    """True unless the command is provably confined to the workspace. Escalates on
    command substitution, an interpreter payload, a redirect whose target is
    outside, or ANY path argument that resolves outside — reads included. Failing
    safe: an unparseable or unrecognized command asks rather than runs."""
    if any(s in command for s in _SUBSTITUTION):
        return True
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return True  # unbalanced quotes etc. — can't reason, so ask
    if not tokens:
        return False
    if _has_interpreter_payload(shlex.split(_OPERATOR_PAD.sub(r" \1 ", command))):
        return True
    # Redirect write targets (scan the raw string so attached forms like `>/x`,
    # `&>/x`, `>|/x` are caught regardless of shlex tokenization).
    for tgt in _REDIRECT_SCAN.findall(command):
        if tgt not in _DEV_SINKS and _path_outside(tgt, workspace):
            return True
    # Any argument (or key=value value) that resolves outside the workspace.
    for tok in tokens:
        for cand in _path_candidates(tok):
            if cand not in _DEV_SINKS and _path_outside(cand, workspace):
                return True
    return False


def _paths(tool_input: dict[str, Any], workspace: Path) -> list[Path]:
    def res(v: Any) -> Path | None:
        if isinstance(v, str) and v:
            # Relative paths resolve against the workspace — matching how the
            # native tools execute them.
            try:
                return (workspace / Path(v).expanduser()).resolve()
            except (OSError, ValueError, RuntimeError):  # a NUL byte, ~nosuchuser, a symlink loop: never inside -> ask
                return Path(repr(v))
        return None

    out = []
    for k in _PATH_KEYS:
        p = res(tool_input.get(k))
        if p is not None:
            out.append(p)
    # copy_files: every src and dest; delete_file: every path. A list input is
    # checked whole — one entry outside the workspace makes the call ask.
    files = tool_input.get("files")
    if isinstance(files, list):
        for f in files:
            if isinstance(f, dict):
                for k in ("src", "dest"):
                    p = res(f.get(k))
                    if p is not None:
                        out.append(p)
    paths = tool_input.get("paths")
    if isinstance(paths, list):
        for v in paths:
            p = res(v)
            if p is not None:
                out.append(p)
    return out


def _bundled_skill_copy(tool_input: dict[str, Any], workspace: Path) -> bool:
    """A ``copy_files`` call whose every source is a FILE inside an installed curated skill root (Dream's own
    ``skills/<name>/`` trees, compared by real path so a symlink out of one does not count) and whose every destination
    lies inside the workspace, with no ``move`` (a move would delete the bundled file). That is a skill bringing its
    own bundle into the project -- DREAM-102's ``glue.py`` -- and nothing else."""
    from .. import config

    files = tool_input.get("files")
    if not isinstance(files, list) or not files:
        return False
    roots: list[Path] = []
    for root in config.bundled_skill_dirs():
        try:
            if root.is_dir():
                roots.append(root.resolve())
        except OSError:
            continue
    for entry in files:
        if not isinstance(entry, dict) or entry.get("move") or not entry.get("src") or not entry.get("dest"):
            return False
        try:
            src = (workspace / Path(str(entry["src"])).expanduser()).resolve(strict=True)
            dest = (workspace / Path(str(entry["dest"])).expanduser()).resolve()
        except (OSError, ValueError, RuntimeError):
            return False
        if not src.is_file() or not any(src.is_relative_to(root) for root in roots):
            return False
        if not dest.is_relative_to(workspace):
            return False
    return True


def _shell_lexer(command: str) -> shlex.shlex:
    """Shell-like tokens: quotes honoured, operators apart. shlex takes `#` for a comment even inside a
    word, where bash does not: `echo a#b; rm -rf build` lost its rm to every check (DREAM-112 gate).
    Comments are therefore off: a real comment's words are read as commands, which only ever makes a
    check stricter."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    return lexer


def _shell_parts(command: str) -> list[list[str]]:
    """Command-position tokens, not a claim to understand arbitrary shell code."""
    lexer = _shell_lexer(command)
    parts: list[list[str]] = [[]]
    for token in lexer:
        if token and all(c in ";&|()\n" for c in token):
            parts.append([])
        else:
            parts[-1].append(token)
    return [p for p in parts if p]


def _shell_effect(command: str, workspace: Path, *, network: bool = False) -> tuple[str, list[Path]]:
    """Recognize explicit consequential operations, including common wrappers.

    Opaque programs still require OS containment. This detector is deliberately
    not presented as a sandbox, nor does it infer safety from a script's name.
    network: the command runs contained with public internet, so a plain
    download (curl, wget) is routine; the rest of the command is still checked.
    """
    try:
        parts = _shell_parts(command)
    except ValueError:
        return "unparseable shell command", []
    deletions: list[Path] = []
    for part in parts:
        words = list(part)
        while words and ("=" in words[0] or words[0] in {
                "command", "exec", "env", "nohup", "time", "builtin", "xargs"}
                or words[0].startswith("-")):
            words.pop(0)
        if not words:
            continue
        verb = Path(words[0]).name
        args = words[1:]
        if verb in {"timeout", "nice", "stdbuf"}:
            # Recurse after ordinary wrapper options. Unknown wrapper forms
            # remain contained opaque code; this covers routine timed commands.
            nested = list(args)
            while nested and nested[0].startswith("-"):
                option = nested.pop(0)
                if option in {"-s", "--signal", "-k", "--kill-after", "-n", "--adjustment"} and nested:
                    nested.pop(0)
            if verb == "timeout" and nested:
                nested.pop(0)
            reason, _ = _shell_effect(shlex.join(nested), workspace, network=network)
            if reason:
                return reason, []
        if verb in {"rm", "rmdir", "unlink", "shred"}:
            raw = [a for a in args if not a.startswith("-")]
            # A red-team exception requires a single, literal, direct deletion.
            literal = len(parts) == 1 and words == part and raw and not any(
                any(c in a for c in "$`*?[]{}<>~") for a in raw)
            if literal:
                deletions = [(workspace / a).resolve() for a in raw]
            return "deletes files", deletions
        if verb == "git":
            # Read verbs tolerate git's global -C/-c options; unknown forms ask.
            safe = {"status", "diff", "log", "show", "ls-files", "rev-parse",
                    "rev-list", "describe", "grep", "blame", "cat-file", "help"}
            if not args or args[0] not in safe or any(
                    a in {"--ext-diff", "--textconv"} or a.startswith("--output") for a in args):
                return "changes or publishes repository state", []
        if network and verb in {"curl", "wget"}:
            continue
        if verb in {"curl", "wget", "ssh", "scp", "sftp", "rsync", "gh", "ftp",
                    "sudo", "su", "doas", "systemctl", "service", "reboot", "shutdown",
                    "mount", "umount", "dd", "mkfs", "fdisk", "wipefs", "kill", "pkill",
                    "killall", "chmod", "chown", "chgrp", "crontab", "at"}:
            return "network, system, or destructive operation", []
        if verb in {"npm", "pnpm", "yarn", "cargo", "twine", "docker", "podman"} and any(
                a in {"publish", "push", "login", "logout", "deploy", "upload"} for a in args):
            return "publishes or changes credentials", []
        if verb in {"find", "sed"} and any(a in {"-delete", "-exec", "-execdir", "e"} for a in args):
            return "embedded shell or deletion", []
        if _names_binary(verb, _INTERPRETERS):
            for i, arg in enumerate(args):
                if arg in _INLINE_CODE_FLAGS and i + 1 < len(args) and verb in {"bash", "sh", "dash", "zsh"}:
                    reason, _ = _shell_effect(args[i + 1], workspace, network=network)
                    if reason:
                        return reason, []
    return "", []


def _static_shell_read(command: str) -> bool:
    """Legacy advice for simple inspection only; never evidence of containment."""
    try:
        parts = _shell_parts(command)
    except ValueError:
        return False
    if len(parts) != 1 or any(s in command for s in (*_SUBSTITUTION, ">", "<", "\n")):
        return False
    words = parts[0]
    if words[0] in {"ls", "pwd", "cat", "head", "tail", "wc", "grep", "rg", "stat", "file", "echo", "true", "false"}:
        return True
    return words[0] == "git" and len(words) > 1 and words[1] in {
        "status", "diff", "log", "show", "ls-files", "rev-parse", "describe"}


_READ_VERBS = frozenset({
    "cd", "pwd", "ls", "cat", "head", "tail", "less", "more", "wc", "grep", "egrep", "fgrep", "rg",
    "find", "stat", "file", "du", "df", "echo", "printf", "true", "false", "which", "type", "env",
    "sort", "uniq", "cut", "tr", "diff", "cmp", "md5sum", "sha256sum", "basename", "dirname",
    "realpath", "readlink", "date", "nl", "column", "jq", "tree", "test", "[",
})


def _options(args: list[str], short: str = "", short_value: str = "", long: frozenset = frozenset(),
             long_value: frozenset = frozenset(), optional: str = "", long_optional: frozenset = frozenset(),
             ) -> list[str] | None:
    """The operands of `args`, or None when an option is not one of the listed read options.
    `short`/`long` take no value; `short_value`/`long_value` take one (attached, `=`, or the next word);
    `optional`/`long_optional` take one only when attached (GNU optional arguments), never the next word."""
    operands: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg == "--":
            return operands + args[i:]
        if arg.startswith("--"):
            name, eq, _ = arg.partition("=")
            if name in long_value:
                i += 0 if eq else 1
            elif name not in long_optional and (name not in long or eq):
                return None
        elif arg.startswith("-") and arg != "-":
            for k, flag in enumerate(arg[1:]):
                if flag in short_value:
                    i += 0 if arg[k + 2:] else 1     # the rest of the cluster is its value, else the next word
                    break
                if flag in optional:
                    break                            # the rest of the cluster, if any, is its value
                if flag not in short:
                    return None
        else:
            operands.append(arg)
    return operands


def _long(*names: str) -> frozenset:
    return frozenset("--" + n for n in names)


_SORT_LONG = _long("numeric-sort", "reverse", "unique", "ignore-case", "human-numeric-sort", "version-sort",
                   "general-numeric-sort", "month-sort", "random-sort", "stable", "merge",
                   "zero-terminated", "dictionary-order", "ignore-leading-blanks", "ignore-nonprinting")
_SORT_VALUE = _long("key", "field-separator", "buffer-size", "parallel", "sort")
_UNIQ_LONG = _long("count", "repeated", "unique", "ignore-case", "zero-terminated")
_UNIQ_VALUE = _long("skip-fields", "skip-chars", "check-chars")
_TREE_LONG = _long("du", "si", "dirsfirst", "filesfirst", "noreport", "gitignore", "prune", "matchdirs",
                   "ignore-case", "info", "help", "version", "fromfile", "metafirst", "nolinks", "inodes", "device")
_TREE_VALUE = _long("charset", "filelimit", "sort", "timefmt")
_FILE_LONG = _long("brief", "mime", "mime-type", "mime-encoding", "dereference", "no-dereference", "keep-going",
                   "uncompress", "special-files", "no-pad", "print0", "raw", "extension", "apple")
_FILE_VALUE = _long("exclude", "magic-file", "files-from", "separator", "parameter")
_DATE_VALUE = _long("date", "reference", "file", "rfc-3339")
_DATE_LONG = _long("utc", "universal", "rfc-email", "rfc-2822", "debug")
# find: predicates that read, and those of them that take a value. -delete, -exec, -execdir, -ok, -okdir,
# -fprint, -fprint0, -fprintf, -fls and anything unknown are not reads.
_FIND_VALUE = frozenset({"-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename", "-regex", "-iregex",
                         "-regextype", "-type", "-xtype", "-size", "-mtime", "-mmin", "-atime", "-amin", "-ctime",
                         "-cmin", "-newer", "-anewer", "-cnewer", "-perm", "-user", "-group", "-uid", "-gid",
                         "-links", "-inum", "-samefile", "-maxdepth", "-mindepth", "-fstype", "-lname", "-ilname",
                         "-used", "-printf", "-D", "-context"})
_FIND_FLAG = frozenset({"-print", "-print0", "-ls", "-prune", "-quit", "-not", "!", "-a", "-and", "-o", "-or",
                        "-true", "-false", "-empty", "-readable", "-writable", "-executable", "-nouser", "-nogroup",
                        "-depth", "-mount", "-xdev", "-follow", "-daystart", "-noleaf", "-ignore_readdir_race",
                        "-noignore_readdir_race", "-L", "-H", "-P", "-O0", "-O1", "-O2", "-O3"})
# sed: only scripts made of printing, deleting, quitting and s/// without the w or e flags. Anything else
# (w, W, e, r, y, other delimiters, several lines) is not known to be a read.
_SED_ADDRESS = r"(?:\d+|\$|/(?:[^/\\\n]|\\.)*/I?)"
_SED_COMMAND = (rf"(?:{_SED_ADDRESS}(?:\s*,\s*{_SED_ADDRESS})?\s*!?\s*)?"
                r"(?:[pdq=]|s/(?:[^/\\\n]|\\.)*/(?:[^/\\\n]|\\.)*/[gpiI0-9]*)")
_SED_SCRIPT = re.compile(rf"\s*{_SED_COMMAND}\s*(?:;\s*{_SED_COMMAND}\s*)*;?\s*")
_GIT_BRANCH_LIST = frozenset({"-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose", "--show-current", "-i",
                              "--ignore-case", "--no-color", "--no-column", "--omit-empty"})
_GIT_BRANCH_VALUE = frozenset({"--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--sort",
                               "--format"})


def _sed_read(args: list[str]) -> bool:
    scripts: list[str] = []
    files: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg in ("-e", "--expression"):
            scripts.append(args[i] if i < len(args) else "")
            i += 1
        elif arg.startswith("--expression="):
            scripts.append(arg.split("=", 1)[1])
        elif arg in _long("quiet", "silent", "regexp-extended", "separate", "null-data", "posix", "debug"):
            continue
        elif arg.startswith("-") and arg != "-":
            for k, flag in enumerate(arg[1:]):
                if flag == "e":                      # -ne SCRIPT: the rest of the cluster, else the next word
                    scripts.append(arg[k + 2:] or (args[i] if i < len(args) else ""))
                    i += 0 if arg[k + 2:] else 1
                    break
                if flag not in "nErsz":              # -i, -f, -l, -u ... are not reads here
                    return False
        else:
            files.append(arg)
    if not scripts:
        if not files:
            return False
        scripts.append(files.pop(0))
    return all(_SED_SCRIPT.fullmatch(script) for script in scripts)


def _awk_read(args: list[str]) -> bool:
    """-F and -v only; the program may not write (a `>` after its first print, which also rejects some
    comparisons), pipe (`|`), run (system) or load code. Any `@` counts as writing: gawk's @load and
    @include, and its indirect call `@f()`, which reaches a built-in named by a string built at run time
    (`f = "sys" "tem"; @f("touch x")`), so no word list can see it (DREAM-112 gate round 2)."""
    i = 0
    while i < len(args) and args[i] != "--":
        if args[i] in ("-F", "-v"):
            i += 2
        elif args[i].startswith(("-F", "-v")):
            i += 1
        elif args[i].startswith("-"):
            return False
        else:
            break
    i += i < len(args) and args[i] == "--"
    if i >= len(args):
        return False
    program = args[i]
    printing = re.search(r"\bprintf?\b", program)
    return not (re.search(r"(?<!\|)\|(?!\|)", program) or any(w in program for w in ("system", "@"))
                or (printing and ">" in program[printing.start():]))


def _find_read(args: list[str]) -> bool:
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in _FIND_VALUE or re.fullmatch(r"-newer[aBcm][aBcmt]", arg):
            i += 2
        elif arg in _FIND_FLAG or not arg.startswith("-"):
            i += 1
        else:
            return False
    return True


def _date_read(args: list[str]) -> bool:
    """Formats (+...) and reading options only: -s/--set, or a bare date operand, would set the clock."""
    operands = _options(args, short="uR", short_value="drf", long=_DATE_LONG, long_value=_DATE_VALUE,
                        optional="I", long_optional=_long("iso-8601"))
    return operands is not None and all(o.startswith("+") for o in operands)


def _uniq_read(args: list[str]) -> bool:
    """At most one operand: uniq's second operand is the file it writes."""
    operands = _options(args, short="cdDuiz", short_value="fsw", long=_UNIQ_LONG, long_value=_UNIQ_VALUE,
                        long_optional=_long("all-repeated", "group"))
    return operands is not None and len(operands) <= 1


def _git_read(args: list[str]) -> bool:
    """git subcommands that only look. Global options (-C, -c, --no-pager ...) are not accepted: -c can
    point a hook or pager at any program."""
    if not args:
        return False
    sub, rest = args[0], args[1:]
    if sub in {"status", "ls-files", "rev-parse", "describe", "blame"}:
        return True
    if sub in {"diff", "log", "show"}:
        return not any(a == "--output" or a.startswith("--output=") or a == "--ext-diff" for a in rest)
    if sub == "branch":
        listing, named, i = False, False, 0
        while i < len(rest):
            arg = rest[i]
            name = arg.partition("=")[0]
            if arg in ("--list", "-l"):
                listing = True
            elif name in _GIT_BRANCH_VALUE:
                i += 0 if "=" in arg else 1
            elif arg in _GIT_BRANCH_LIST or arg.startswith(("--color", "--column", "--abbrev")):
                pass
            elif arg.startswith("-"):
                return False                         # -d, -D, -m, -M, -c, -C, -f, -u, --set-upstream-to ...
            else:
                named = True                         # a name creates a branch unless listing
            i += 1
        return listing or not named
    if sub == "remote":
        while rest and rest[0] in ("-v", "--verbose"):
            rest = rest[1:]
        if not rest:
            return True
        if rest[0] == "show":
            return all(a == "-n" or not a.startswith("-") for a in rest[1:])
        if rest[0] == "get-url":
            return all(a in ("--push", "--all") or not a.startswith("-") for a in rest[1:])
        return False                                 # add, remove, rename, set-url, prune, update ...
    return False


# Read verbs that can also write, run or change something, and what makes one of their calls a read.
_READ_CHECKS = {
    "sed": _sed_read,
    "awk": _awk_read,
    "find": _find_read,
    "git": _git_read,
    "date": _date_read,
    "sort": lambda a: _options(a, short="bdfghiMnrRsuVzcCm", short_value="ktS", long=_SORT_LONG,
                               long_value=_SORT_VALUE, long_optional=_long("check")) is not None,
    "uniq": _uniq_read,
    "tree": lambda a: _options(a, short="adfilxpsuhgDFqNQrtcUvCnASJX", short_value="LPIH", long=_TREE_LONG,
                               long_value=_TREE_VALUE) is not None,
    "file": lambda a: _options(a, short="bhiLkzZsNE0", short_value="mfeFP", long=_FILE_LONG,
                               long_value=_FILE_VALUE) is not None,
    "rg": lambda a: not any(x.startswith("--pre") for x in a),
    "less": lambda a: not any(x.startswith(("-", "+")) for x in a),
    "more": lambda a: not any(x.startswith(("-", "+")) for x in a),
}


def shell_read_only(command: str) -> bool:
    """Every segment of `command` only reads. Conservative by construction, because a contained
    read-only command runs without asking in every mode but plan (fix #41): a verb must be on the read
    list; a read verb that can also write (find, sed, awk, sort, uniq, tree, date, file, git, rg, less)
    must use only its reading forms, and an option it does not know counts as writing; any output
    redirection (> >> >| >& &> <>, 2>/dev/null included), substitution ($( ` <( >() or sudo makes the
    command not a read. Also used by the progress guard (fix #46) and the half-cost rounds (fix #17);
    never evidence of containment."""
    if any(s in command for s in _SUBSTITUTION):
        return False                                 # a substitution runs a command of its own
    try:
        tokens = list(_shell_lexer(command))
        parts = _shell_parts(command)
    except ValueError:
        return False
    if not parts or any(token and set(token) <= set(";&|()<>\n") and ">" in token for token in tokens):
        return False                                 # `>` as a shell operator, not inside a quoted program
    for words in parts:
        verb = words[0]
        if verb in {"env", "command", "nice", "time", "timeout"}:
            words = words[1:]
            while words and (words[0].startswith("-") or "=" in words[0]) and verb in {"env", "timeout", "nice"}:
                words = words[1:]
            if not words:
                return False
            verb = words[0]
        check = _READ_CHECKS.get(verb)
        if check is not None:
            if not check(words[1:]):
                return False
        elif verb not in _READ_VERBS:
            return False
    return True


def _bounded_output_cleanup(command: str, workspace: Path) -> bool:
    """Recognize a narrow output convention, not proof of file provenance.

    Only direct nonrecursive image cleanup qualifies. Inspect every compound
    segment independently so an earlier rm never hides a later consequential verb.
    The caller must supply actual containment; arbitrary renderer code is not
    made safe by this classification.
    """
    if any(s in command for s in (*_SUBSTITUTION, "\\\n", "\\\r\n")):
        return False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        tokens = list(lexer)
        if any(token in {"&", "||", "(", ")", "{", "}"} for token in tokens):
            return False
        parts = _shell_parts(command)
    except ValueError:
        return False
    found = False
    scanned = 0
    for part in parts:
        words = list(part)
        # Shell's ordinary time prefix is sufficient for the renderer workflow.
        # Other wrapper options, assignments and control syntax are ambiguous.
        if words[0] == "time":
            words = words[1:]
        if (not words or words[0].startswith(("-", ">", "<", "&"))
                or words[0].isdigit() or "=" in words[0]):
            return False
        verb = Path(words[0]).name
        if verb in {"command", "exec", "env", "nohup", "builtin", "xargs", "timeout",
                    "nice", "stdbuf", "eval", "source", ".", "if", "for", "while",
                    "until", "case", "function", "select", "trap", "alias", "set", "export",
                    "!", "coproc", "time", "pushd", "popd", "busybox", "toybox",
                    "shopt", "hash", "unset", "declare", "typeset", "readonly", "local",
                    "enable", "unalias", "ulimit", "setsid", "taskset", "chrt", "ionice", "flock"}:
            return False
        if any("$" in word for word in words) and not (
                verb == "echo" and all("$" not in word.replace("$?", "") for word in words)):
            return False
        if verb == "cd":
            if len(words) != 2 or words[1].startswith("-") or (workspace / words[1]).resolve() != workspace:
                return False
            continue
        if verb not in {"rm", "unlink"}:
            if _shell_effect(shlex.join(words), workspace)[0] or _has_interpreter_payload(words):
                return False
            continue
        if words != part:  # No wrappers around cleanup itself.
            return False
        if words[0] not in {"rm", "/bin/rm", "/usr/bin/rm", "unlink", "/bin/unlink", "/usr/bin/unlink"}:
            return False
        args = words[1:]
        force = False
        while args and args[0].startswith("-"):
            option, args = args[0], args[1:]
            if option == "--":
                break
            if verb != "rm" or option not in {"-f", "--force"}:
                return False
            force = True
        if not args or (verb == "unlink" and len(args) != 1):
            return False
        for raw in args:
            target = workspace / raw
            parent = target.parent
            leaf = target.name
            if (any(c in raw for c in "$`?[]{}<>~") or "*" in str(parent)
                    or target.suffix.lower() not in {".png", ".jpg", ".jpeg", ".exr"}
                    or ("*" in leaf and leaf != "*" + target.suffix)):
                return False
            try:
                relative = parent.relative_to(workspace)
                if (not relative.parts or not any(p in {"frames", "renders", "outputs"} for p in relative.parts)
                        or any(p.startswith(".") for p in relative.parts)
                        or parent.resolve() != parent or not parent.is_dir()):
                    return False
                matches = 0
                with os.scandir(parent) as entries:
                    for entry in entries:
                        scanned += 1
                        if scanned > 1024:
                            return False
                        if fnmatch.fnmatchcase(entry.name, leaf):
                            if entry.name.startswith(".") or entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                                return False
                            matches += 1
                if not matches and not force:
                    return False
            except (OSError, ValueError):
                return False
        found = True
    return found


def decide(
    tool_name: str, tool_input: dict[str, Any], mode: str, workspace: Path, *,
    execution_scope: ExecutionScope | None = None,
    execution_capability: SandboxCapability | None = None,
) -> tuple[str, str]:
    """→ ("allow" | "ask" | "deny", reason). The single permission authority for
    both backends; the reason string is shown when a prompt or denial happens.
    Pass execution_capability ONLY when this tool actually uses that executor;
    passing metadata to an SDK/CLI-owned Bash tool does not enforce containment.

    A reason beginning "outside workspace" marks a boundary decision. File writes
    still ask individually. The TUI may remember explicit native Bash approvals
    by exact command, session, scope and sandbox/host execution choice."""
    workspace = Path(workspace).resolve()
    cap = capability(tool_name)
    scoped = execution_scope is not None and execution_scope.workspace == workspace
    if scoped:
        from .execution import ExecutionRefused
        try:
            execution_scope.validate()
        except ExecutionRefused as exc:
            return "deny", str(exc)
    contained = bool(scoped and execution_capability is not None
                     and execution_capability.enforces(execution_scope))

    # Read-only tools, and Dream editing its own memory, are free in every mode.
    if cap in AUTO_CAPS:
        return "allow", ""

    if cap == WRITE:
        paths = _paths(tool_input, workspace)
        outside = [p for p in paths if not p.is_relative_to(workspace)]
        if mode == "plan":
            return "deny", "plan mode — no changes"
        if tool_name.split("__")[-1] == "run_script":
            # This builtin ALWAYS runs its JS and Python helpers in the executor.
            # A missing sandbox refuses execution; approval cannot bypass it.
            return ("allow", "contained script") if mode in ("accept-edits", "auto") else ("ask", "workspace script")
        if scoped and execution_scope.red_team and not execution_scope.allows_deletion(paths):
            return "deny", "outside workspace execution scope: red-team writes require an explicit target"
        if outside and _short(tool_name) == "copy_files" and _bundled_skill_copy(tool_input, workspace):
            # DREAM-102: a curated skill's bundled file coming into the project is a workspace write, not an outside one
            return ("allow", "bundled skill file into the workspace") if mode in ("accept-edits", "auto") else ("ask", "write in workspace")
        if outside:
            return "ask", f"outside workspace: {outside[0]}"
        if mode in ("accept-edits", "auto"):
            return "allow", ""
        return "ask", "write in workspace"

    if cap == DESTRUCTIVE:
        if mode == "plan":
            return "deny", "plan mode — no changes"
        paths = _paths(tool_input, workspace)
        outside = [p for p in paths if not p.is_relative_to(workspace)]
        if scoped and execution_scope.red_team and not execution_scope.allows_deletion(paths):
            return "deny", "outside workspace execution scope: red-team deletion requires an explicit target"
        if outside:
            return "ask", f"outside workspace: deletes {outside[0]}"
        if mode == "auto" and scoped and execution_scope.allows_deletion(paths):
            return "allow", "explicit red-team deletion scope"
        raw = tool_input.get("paths")
        given = [str(v) for v in raw if isinstance(v, str)] if isinstance(raw, list) else []
        shown = ", ".join(given[:3]) + (" …" if len(given) > 3 else "")
        return "ask", f"deletes: {shown or '?'}"

    if cap == SHELL:
        if mode == "plan":
            return "deny", "plan mode — no commands"
        command = str(tool_input.get("command") or "")
        effect, deletions = _shell_effect(command, workspace,
                                          network=contained and execution_scope.network)
        if effect:
            if mode == "auto" and contained and execution_scope.allows_deletion(deletions):
                return "allow", "explicit red-team deletion scope"
            from .. import config
            if (mode == "auto" and contained and not execution_scope.red_team
                    and tool_name in {"run_bash", f"mcp__{config.MCP_SERVER_NAME}__run_bash"}
                    and _bounded_output_cleanup(command, workspace)):
                return "allow", "bounded image-output cleanup inside workspace containment"
            if _shell_unconfined(command, workspace):
                return "ask", f"outside workspace (shell): {command[:60]}"
            return "ask", effect
        if contained and shell_read_only(command):
            # Fix #41 (2026-09-21: 145 s of the first 264 s waited on ls/grep/cat): a contained command
            # whose every segment only reads runs without asking, in every mode but plan.
            return "allow", "read-only command inside workspace containment"
        if contained:
            return ("allow", "contained command") if mode == "auto" else ("ask", "shell command")
        # The workspace boundary applies to shell too. A command that isn't
        # provably confined to the workspace asks in EVERY mode (auto included),
        # since cwd can't confine an absolute path, a redirect, or `bash -c`.
        if _shell_unconfined(command, workspace):
            return "ask", f"outside workspace (shell): {command[:60]}"
        if mode == "auto" and _static_shell_read(command):
            return "allow", ""
        return "ask", "shell command requires verified containment"

    if cap == CONTAINED:
        # Its executor is the enforced sandbox itself; without one it never runs at all.
        if mode == "plan":
            return "deny", "plan mode — no changes"
        if scoped and execution_scope.red_team:
            return "deny", "tool has no enforced red-team execution boundary"
        if mode in ("accept-edits", "auto"):
            return "allow", "confined to the workspace by the live-Blender sandbox"
        return "ask", "code in live Blender (confined to the workspace)"

    # No executor contract is supplied for arbitrary MCP/custom Python here.
    if mode == "plan":
        return "deny", "plan mode — no changes"
    if scoped and execution_scope.red_team:
        return "deny", "tool has no enforced red-team execution boundary"
    return "ask", "tool may change things"


def classify_file_activity(
    tool_name: str, tool_input: dict[str, Any], workspace: Path
) -> tuple[str, str] | None:
    """Best-effort session accounting for the status panel:
    → ("created" | "edited" | "deleted", display_name) or None.
    Write to a new path counts as created, to an existing one as edited;
    an `rm` inside a shell command counts as deleted (heuristic, display-only)."""
    short = tool_name.split("__")[-1]
    if short in _SHELL:
        cmd = str(tool_input.get("command") or "")
        if _RM_RE.search(cmd):
            return "deleted", "(via shell)"
        return None
    if short in _DESTRUCTIVE:
        paths = _paths(tool_input, workspace)
        return ("deleted", paths[0].name) if paths else None
    if short not in _WRITE:
        return None
    paths = _paths(tool_input, workspace)
    if not paths:
        return None
    if short == "copy_files":
        # paths alternate src, dest; what a copy creates is its destination.
        dests = paths[1::2]
        return ("created", dests[0].name) if dests else None
    p = paths[0]
    if short in ("Write", "write_file") and not p.exists():
        return "created", p.name
    return "edited", p.name
