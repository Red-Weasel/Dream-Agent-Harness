"""DREAM-252: what a `task` call may declare about its worker, and the bounded check of the files it must leave.

Two optional fields on the `task` tool: ``required_tools`` (names the role must really have) and ``outputs``
(workspace-relative files the worker must produce). This module parses them and checks the files; it grants nothing:
a contract can refuse a run or fail its result, never add a tool, a path or a permission.

What a passed check says: when it ran, the path was a regular, single-link file under the workspace, reached without
following any symbolic link, no larger than MAX_OUTPUT_BYTES, different from what was there before the worker started,
and its bytes -- read once, hashed, never executed -- passed the format check. What it does not say: who wrote the file
(any process that can write the workspace could have, the worker among them), that its content is right, or that it
stays as it was after the check.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from typing import Any

MAX_OUTPUTS = 32                 # outputs one task may declare
MAX_OUTPUT_BYTES = 1 << 20       # the most the check reads of one output (1 MiB); a larger file is refused unread
MAX_KEYS = 64                    # required_keys of one output
MAX_TOOLS = 64                   # required_tools of one task
MAX_PATH_CHARS = 512
MAX_PATH_PARTS = 32
MAX_REPORT_CHARS = 8000          # the most one check's report takes of a task result, however many outputs it lists
MAX_SNAPSHOT_BYTES = 512 << 10   # host checkpoint, distinct from a model-facing report
FORMATS = ("json", "text")
_NAME = re.compile(r"[A-Za-z0-9_.:\-]{1,128}\Z")        # a tool name or a JSON key that is safe to repeat
_FIELDS = frozenset({"path", "format", "required_keys"})
# A floor, not a policy: Dream's and git's own state and the usual credential stores are never a worker's declared
# output. Compared case-insensitively. `.ua/` (the Understand skill's batch files) is deliberately not here.
# `.codex` and `.claude` are the CLI adapters' homes (auth.json, .credentials.json). Dream's own configured files --
# data/config/extensions.json, data/runtime-settings.json, var/loops, mcp.json, wherever the environment put them --
# are not names but places: _authority_inside finds the ones that lie inside the workspace being checked, and
# _in_credential_root / _run_record the ones the workspace itself is, or sits inside.
_PROTECTED_PARTS = frozenset({".dream", ".git", ".remember", ".ssh", ".gnupg", ".aws", ".kube", ".docker",
                              ".codex", ".claude"})
_PROTECTED_NAMES = frozenset({".env", ".netrc", ".npmrc", ".pypirc", ".git-credentials", "credentials.json",
                              ".credentials.json", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"})
# Reads that never follow a link need directory-relative opens; without them (Windows) outputs are refused, not guessed.
SAFE_READS = (all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK"))
              and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd
              and os.stat in os.supports_follow_symlinks)


class ContractError(ValueError):
    """A contract that cannot be honoured; `reason` is its typed code."""

    def __init__(self, reason: str, text: str) -> None:
        super().__init__(text)
        self.reason = reason


@dataclass(frozen=True)
class Output:
    path: str                    # normalised, workspace-relative, '/'-separated
    format: str
    required_keys: tuple[str, ...]


@dataclass(frozen=True)
class Contract:
    required_tools: tuple[str, ...]
    outputs: tuple[Output, ...]


def refusal(reason: str, text: str) -> str:
    """The result of a `task` call refused before its worker started."""
    return (f"Error: task contract refused before the worker started (reason={reason}): {text} "
            "No request was sent and no tool ran.")


def _invalid(text: str) -> ContractError:
    return ContractError("invalid_contract", text)


def _plain(value: str) -> bool:
    """Whether every character is printable and the whole can be handed to the filesystem. str.isprintable is false
    for the Unicode categories Other (C0, DEL and C1 controls, format characters such as zero-width and bidi marks,
    unpaired surrogates, private-use and unassigned code points) and Separator (line, paragraph, every space but the
    plain one). The encode is the one an os call would do; done here, it fails before any file is touched."""
    if not all(char.isprintable() for char in value):
        return False
    try:
        value.encode("utf-8")
        os.fsencode(value)
    except ValueError:                                   # UnicodeError is a ValueError
        return False
    return True


def _clean_path(value: Any, where: str) -> str:
    """A declared path, normalised. Messages name the field, never the value: a refused value is not repeated."""
    if not isinstance(value, str) or not value:
        raise _invalid(f"{where}.path must be a non-empty string.")
    if len(value) > MAX_PATH_CHARS or "\\" in value or not _plain(value):
        raise _invalid(f"{where}.path must be at most {MAX_PATH_CHARS} characters, '/'-separated, of printable "
                       "characters the filesystem can encode: no control, formatting or separator characters (a "
                       "plain space is fine), no unpaired surrogates, no backslashes.")
    if value.startswith(("/", "~")) or re.match(r"[A-Za-z]:", value):
        raise _invalid(f"{where}.path must be relative to the workspace, not absolute or under a home folder.")
    if value.endswith("/"):
        raise _invalid(f"{where}.path must name a file, not a folder.")
    raw = value.split("/")
    if ".." in raw:
        raise _invalid(f"{where}.path must stay inside the workspace: '..' is not allowed.")
    parts = [part for part in raw if part not in ("", ".")]
    if not parts:
        raise _invalid(f"{where}.path must name a file inside the workspace, not the workspace itself.")
    if len(parts) > MAX_PATH_PARTS:
        raise _invalid(f"{where}.path is nested deeper than {MAX_PATH_PARTS} folders.")
    lowered = [part.lower() for part in parts]
    if _PROTECTED_PARTS & set(lowered) or lowered[-1] in _PROTECTED_NAMES or lowered[-1].startswith(".env."):
        raise _invalid(f"{where}.path is inside Dream's, git's or a credential store's own files: those are never a "
                       "worker's declared output.")
    return "/".join(parts)


def _names(value: Any, where: str, limit: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _invalid(f"{where} must be a list of names.")
    if len(value) > limit:
        raise _invalid(f"{where} holds more than {limit} names.")
    if not all(isinstance(name, str) and _NAME.match(name) for name in value):
        raise _invalid(f"{where} must hold only names of letters, digits and _ . : - (1 to 128 characters).")
    return tuple(dict.fromkeys(value))


def parse_contract(required_tools: Any = None, outputs: Any = None) -> Contract:
    """The contract of one `task` call; None for a field means it was not given. Raises ContractError."""
    tools = () if required_tools is None else _names(required_tools, "required_tools", MAX_TOOLS)
    if outputs is None:
        return Contract(tools, ())
    if not isinstance(outputs, list):
        raise _invalid("outputs must be a list of {\"path\", \"format\", \"required_keys\"} objects.")
    if len(outputs) > MAX_OUTPUTS:
        raise _invalid(f"outputs holds more than {MAX_OUTPUTS} files; split the task.")
    parsed, seen = [], set()
    for index, item in enumerate(outputs):
        where = f"outputs[{index}]"
        if not isinstance(item, dict):
            raise _invalid(f"{where} must be an object with a \"path\".")
        if set(item) - _FIELDS:
            raise _invalid(f"{where} has a field other than path, format and required_keys; an output is only read "
                           "and checked, nothing in it is run.")
        if "path" not in item:
            raise _invalid(f"{where} has no \"path\".")
        path = _clean_path(item["path"], where)
        keys = () if item.get("required_keys") is None else _names(item["required_keys"], f"{where}.required_keys", MAX_KEYS)
        kind = item.get("format")
        if kind is None:
            kind = "json" if keys else "text"
        if not isinstance(kind, str) or kind not in FORMATS:
            raise _invalid(f"{where}.format must be \"json\" or \"text\".")
        if keys and kind != "json":
            raise _invalid(f"{where}.required_keys applies only to format \"json\".")
        if path.lower() in seen:
            raise _invalid(f"{where}.path names the same file as an earlier output.")
        seen.add(path.lower())
        parsed.append(Output(path, kind, keys))
    return Contract(tools, tuple(parsed))


# ---- Dream's own configured files that lie inside the workspace ----------------------------------------------------

# A loop run's folder is <config.LOOP_DIR>/<run id>. These names directly in it are the harness's own run records
# (the run ledger and its state view, the run lock, the contract the run is graded against, and the goal and log
# the loop writes when it makes the run folder and appends to as it goes); `progress.md` and anything in a
# subfolder (out/result.json) are the worker's. Compared case-folded, and exactly: `catalog.md` is not `log.md`.
# The harness replaces its records atomically through a temporary `.<name>-*` beside the record, so those
# prefixes are records too.
_RUN_RECORDS = frozenset({"state.json", "ledger.jsonl", "contract.md", "goal.md", "log.md", ".lock"})
_RUN_RECORD_PREFIXES = (".state.json-", ".goal.md-", ".log.md-", ".contract.md-")


def _credential_roots() -> list[Any]:
    """The folders that hold credentials or approvals and nothing a worker should declare: the CLI adapters' homes
    (auth.json, .credentials.json) and the folder of Dream's extension trust records. Unlike the other configured
    folders these are closed whole, and wherever the workspace is: inside it, the same folder, or a folder in it."""
    from .. import config
    home = os.path.expanduser("~")
    return [os.environ.get("CODEX_HOME") or os.path.join(home, ".codex"),             # config.skill_dirs reads it so
            os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(home, ".claude"),
            config.DATA_DIR / "config"]


def _configured_authority() -> tuple[list[Any], list[Any]]:
    """(folders, files) this Dream is configured to keep its runtime state, settings and credentials in -- read from
    the modules that own them, so a relocation (DREAM_ROOT, DREAM_EXTENSION_SETTINGS, DREAM_MCP_CONFIG, DREAM_DB,
    DREAM_LIBRARY_DB, DREAM_PLUGINS_DIR, CODEX_HOME, and a config value changed in the process) is what is used.
    Only names are computed here; nothing is opened or read."""
    from .. import config, extensions
    from . import profiles
    folders = [config.DATA_DIR, config.VAR_DIR, config.LOOP_DIR, config.PLUGINS_DIR, config.CUSTOM_TOOLS_DIR,
               *_credential_roots()]
    files = [extensions.settings_path(), profiles.settings_path(), config.MCP_CONFIG_PATH, config.DB_PATH,
             config.LIBRARY_DB, config.VAR_DIR / "moe.json", config.INSTRUCTIONS_FILE]
    return folders, files


def _spellings(location: Any) -> set[str]:
    """The location's absolute spellings, case-normalised: as configured, and with links resolved. Names only."""
    try:
        plain = os.path.abspath(os.path.expanduser(os.fspath(location)))
        return {os.path.normcase(plain), os.path.normcase(os.path.realpath(plain))}
    except (OSError, TypeError, ValueError):
        return set()


def _inside(root: str, location: Any) -> set[tuple[str, ...]]:
    """The location as case-folded parts relative to the workspace `root`, for each of its spellings that lies
    strictly inside the root. One that is the root itself or holds it is not returned: a workspace that sits inside
    Dream's ordinary folders (a loop's run folder under var/loops, a project under data) is not thereby off limits,
    only the configured places inside it are. The places that stay closed whatever the workspace's position --
    credential roots, a run's own records -- are found by _within."""
    found: set[tuple[str, ...]] = set()
    prefix = os.path.normcase(root).rstrip(os.sep) + os.sep
    for spelling in _spellings(location):
        if spelling.startswith(prefix):
            parts = tuple(part.casefold() for part in spelling[len(prefix):].split(os.sep) if part)
            if parts:
                found.add(parts)
    return found


def _within(root: str, location: Any) -> set[tuple[str, ...]]:
    """The other direction: the workspace `root` as case-folded parts relative to the location, for each spelling of
    the location that is the root (the empty tuple) or holds it. Empty when the workspace is neither."""
    found: set[tuple[str, ...]] = set()
    here = os.path.normcase(root).rstrip(os.sep)
    for spelling in _spellings(location):
        spelling = spelling.rstrip(os.sep)
        if here == spelling:
            found.add(())
        elif here.startswith(spelling + os.sep):
            found.add(tuple(part.casefold() for part in here[len(spelling) + 1:].split(os.sep) if part))
    return found


def _authority_inside(root: str) -> tuple[set[tuple[str, ...]], set[tuple[str, ...]]]:
    """(folders, files) of _configured_authority that lie inside this workspace, as relative case-folded parts."""
    folders, files = _configured_authority()
    return ({parts for location in folders for parts in _inside(root, location)},
            {parts for location in files for parts in _inside(root, location)})


def _in_credential_root(root: str) -> bool:
    """Whether the workspace is a credential or approval root, or a folder inside one."""
    return any(_within(root, location) for location in _credential_roots())


def _run_record(offsets: set[tuple[str, ...]], parts: tuple[str, ...]) -> bool:
    """Whether a declared path (case-folded parts) is a harness-managed record of a loop run. `offsets` is where the
    workspace sits under the loops folder (_within): the path is a record when, counted from that folder, it is
    exactly <run id>/<a record's name>. So it holds for a workspace that is the run folder or the loops folder
    itself, and not for progress.md, a subfolder's files, or a workspace deeper inside a run folder."""
    for offset in offsets:
        whole = offset + parts
        if len(whole) == 2 and (whole[1] in _RUN_RECORDS or whole[1].startswith(_RUN_RECORD_PREFIXES)):
            return True
    return False

# ---- the check ------------------------------------------------------------------------------------------------------

class _Failed(Exception):
    def __init__(self, reason: str, text: str, ident: tuple | None = None) -> None:
        super().__init__(text)
        self.reason, self.ident = reason, ident


def _ident(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read(root: str, root_id: tuple | None, path: str) -> tuple[tuple, bytes]:
    """(identity, bytes) of one output. Every component is opened relative to its parent's descriptor and never
    through a link, so a swap of any component during the check cannot lead the read elsewhere; the bytes returned
    are the ones validated and hashed -- the file is not opened a second time."""
    descriptors: list[int] = []
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(fd)
        top = os.fstat(fd)
        if root_id is not None and (top.st_dev, top.st_ino) != root_id:
            raise _Failed("output_outside_workspace", "the workspace folder was replaced while the worker ran")
        parts = path.split("/")
        for index, part in enumerate(parts):
            seen = os.stat(part, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(seen.st_mode):
                raise _Failed("output_outside_workspace", "a symbolic link is on its path; the check follows no link, "
                              "so the file cannot be confirmed to be inside the workspace")
            if index < len(parts) - 1:
                if not stat.S_ISDIR(seen.st_mode):
                    raise _Failed("output_missing", "a parent of this path is not a folder")
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            else:
                if not stat.S_ISREG(seen.st_mode):
                    raise _Failed("output_invalid", "not a regular file (a folder, pipe, socket or device is there)")
                fd = os.open(part, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            descriptors.append(fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise _Failed("output_invalid", "not a regular file")
        ident = _ident(info)
        if info.st_nlink != 1:
            raise _Failed("output_invalid", "the file has more than one hard link (or was removed during the check), "
                          "so it may also be a file elsewhere", ident)
        if info.st_size > MAX_OUTPUT_BYTES:
            raise _Failed("output_too_large", f"{info.st_size:,} bytes; the check reads at most "
                          f"{MAX_OUTPUT_BYTES:,}, so it was not read", ident)
        chunks, total = [], 0
        while total <= MAX_OUTPUT_BYTES:                # bounds the read itself: the file may grow after fstat
            chunk = os.read(fd, min(65536, MAX_OUTPUT_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > MAX_OUTPUT_BYTES:
            raise _Failed("output_too_large", f"it grew past the {MAX_OUTPUT_BYTES:,} bytes the check reads", ident)
        if _ident(os.fstat(fd)) != ident:
            raise _Failed("output_invalid", "the file changed while it was being checked (still being written?)")
        return ident, b"".join(chunks)
    except FileNotFoundError:
        raise _Failed("output_missing", "no file at this path") from None
    except ValueError:                                  # a name the filesystem cannot take (parse_contract refuses these)
        raise _Failed("output_invalid", "its path cannot be given to this filesystem") from None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):   # a component became a link or stopped being a folder mid-check
            raise _Failed("output_outside_workspace", "a part of its path was replaced during the check; the check "
                          "follows no link") from None
        raise _Failed("output_invalid", f"it could not be opened for the check ({errno.errorcode.get(exc.errno, 'error')})") from None
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _reject_constant(name: str):
    raise ValueError(f"{name} is not JSON")


def _format_problem(data: bytes, output: Output) -> str | None:
    """Why these bytes fail the output's format, or None. They are decoded and parsed, never run or imported."""
    if not data:
        return "the file is empty"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "not UTF-8 text"
    if "\0" in text:
        return "it holds NUL bytes"
    if output.format == "text":
        return None if text.strip() else "the file is blank"
    try:
        value = json.loads(text, parse_constant=_reject_constant)
    except RecursionError:
        return "its JSON is nested too deeply to check"
    except ValueError as exc:
        where = f" at line {exc.lineno}, column {exc.colno}" if isinstance(exc, json.JSONDecodeError) else ""
        return f"not valid JSON{where}; write the whole file again"
    if output.required_keys:
        if not isinstance(value, dict):
            return "its JSON is not an object, so it has none of the required keys"
        missing = [key for key in output.required_keys if key not in value]
        if missing:
            return "its JSON object lacks the required key(s): " + ", ".join(missing)
    return None


class OutputCheck:
    """One task's declared outputs: what was at each path before the worker started (the constructor), and the check
    after its last reply (finish)."""

    def __init__(self, workspace: Any, outputs: tuple[Output, ...]) -> None:
        self._bind(workspace, outputs)
        self.before = [self._observe(output.path) for output in self.outputs]

    def _bind(self, workspace: Any, outputs: tuple[Output, ...]) -> None:
        """Current workspace and path policy, without observing any output baseline."""
        if not SAFE_READS:
            raise ContractError("no_workspace", "this platform cannot read a file without following links, so "
                                "declared outputs cannot be checked here; send the task without `outputs`.")
        try:
            self.root = os.path.realpath(os.fspath(workspace))
            top = os.stat(self.root)
        except (OSError, TypeError, ValueError):
            raise ContractError("no_workspace", "the session's workspace folder cannot be opened, so declared "
                                "outputs cannot be checked.") from None
        if not stat.S_ISDIR(top.st_mode):
            raise ContractError("no_workspace", "the session's workspace is not a folder.")
        self.root_id = (top.st_dev, top.st_ino)
        self.outputs = outputs
        # Before anything at a declared path is looked at: a path that is one of this Dream's own configured files
        # or folders (settings, extension trust, MCP servers, loops, stores, a CLI's credentials), where they lie
        # inside this workspace, is refused. So is every path when the workspace is itself a credential or approval
        # root or lies inside one, and a loop run's own records when the workspace is that run's folder: where the
        # workspace was put does not open them. The message names the field, never the path or what is there.
        from .. import config
        folders, files = _authority_inside(self.root)
        runs = _within(self.root, config.LOOP_DIR)
        if outputs and _in_credential_root(self.root):
            raise ContractError("invalid_contract", (
                "outputs[0].path is in a workspace that is, or is inside, a folder where this Dream or a CLI keeps "
                "credentials or approvals: no file there is a worker's declared output."))
        for index, output in enumerate(outputs):
            parts = tuple(part.casefold() for part in output.path.split("/"))
            if parts in files or any(parts[:len(folder)] == folder for folder in folders):
                raise ContractError("invalid_contract", (
                    f"outputs[{index}].path is where this Dream keeps its own runtime settings, state or "
                    "credentials: those are never a worker's declared output."))
            if _run_record(runs, parts):
                raise ContractError("invalid_contract", (
                    f"outputs[{index}].path is one of a loop run's harness-managed records: those are never a "
                    "worker's declared output. The run's progress file and files in its subfolders are."))

    def snapshot(self) -> dict[str, Any]:
        """Original baselines for a verified host checkpoint; not an authenticity receipt."""
        before = []
        for item in self.before:
            if item is None:
                before.append({"kind": "missing"})
            elif item == ("other",):
                before.append({"kind": "other"})
            else:
                try:
                    identity, digest = item
                    before.append({"kind": "file", "identity": list(identity), "sha256": digest})
                except (TypeError, ValueError):
                    raise ContractError("invalid_snapshot", "the original output baseline is malformed") from None
        data = {"kind": "dream.output_check_snapshot", "version": 1, "root": self.root,
                "root_id": list(self.root_id), "outputs": self._output_data(self.outputs), "before": before}
        self._decode_snapshot(data)
        return data

    @staticmethod
    def _output_data(outputs: tuple[Output, ...]) -> list[dict[str, Any]]:
        try:
            return [{"path": item.path, "format": item.format, "required_keys": list(item.required_keys)}
                    for item in outputs]
        except (AttributeError, TypeError):
            raise ContractError("invalid_snapshot", "the original output contract is malformed") from None

    @staticmethod
    def _decode_snapshot(data: Any) -> tuple[str, tuple[int, int], tuple[Output, ...], list]:
        def invalid() -> None:
            raise ContractError("invalid_snapshot", "the saved output check is malformed or exceeds its bounds")

        if type(data) is not dict or set(data) != {"kind", "version", "root", "root_id", "outputs", "before"}:
            invalid()
        if data["kind"] != "dream.output_check_snapshot" or type(data["version"]) is not int or data["version"] != 1:
            invalid()
        root = data["root"]
        if type(root) is not str or not root or len(root) > 8192 or not _plain(root) or not os.path.isabs(root):
            invalid()

        def identity(value: Any, size: int) -> tuple[int, ...]:
            if type(value) is not list or len(value) != size:
                invalid()
            for index, part in enumerate(value):
                low = -(1 << 63) if size == 5 and index >= 3 else 0
                if type(part) is not int or not low <= part < (1 << 64):
                    invalid()
            return tuple(value)

        root_id = identity(data["root_id"], 2)
        raw_outputs = data["outputs"]
        if type(raw_outputs) is not list or len(raw_outputs) > MAX_OUTPUTS:
            invalid()
        for output in raw_outputs:
            if (type(output) is not dict or set(output) != _FIELDS or type(output["path"]) is not str
                    or type(output["format"]) is not str or type(output["required_keys"]) is not list
                    or not all(type(key) is str for key in output["required_keys"])):
                invalid()
        try:
            outputs = parse_contract(outputs=raw_outputs).outputs
        except ContractError:
            invalid()
        # parse_contract normalizes paths/keys. A restore format must already be canonical.
        if OutputCheck._output_data(outputs) != raw_outputs:
            invalid()
        raw_before = data["before"]
        if type(raw_before) is not list or len(raw_before) != len(outputs):
            invalid()
        before = []
        for item in raw_before:
            if type(item) is not dict:
                invalid()
            if item == {"kind": "missing"}:
                before.append(None)
            elif item == {"kind": "other"}:
                before.append(("other",))
            elif set(item) == {"kind", "identity", "sha256"} and item["kind"] == "file":
                digest = item["sha256"]
                if digest is not None and (type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                    invalid()
                before.append((identity(item["identity"], 5), digest))
            else:
                invalid()
        try:
            encoded = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, OverflowError):
            invalid()
        if len(encoded) > MAX_SNAPSHOT_BYTES:
            invalid()
        return root, root_id, outputs, before

    @classmethod
    def from_snapshot(cls, workspace: Any, outputs: tuple[Output, ...], snapshot: Any) -> OutputCheck:
        """Restore verified host data without recapturing before-state; rerun current path policy."""
        root, root_id, saved_outputs, before = cls._decode_snapshot(snapshot)
        # Compare normalized exact descriptors, not Python's bool/int equality or just file names.
        expected = cls._output_data(outputs)
        if expected != cls._output_data(saved_outputs):
            raise ContractError("invalid_snapshot", "the saved output contract does not match this worker")
        restored = cls.__new__(cls)
        restored._bind(workspace, saved_outputs)
        if restored.root != root or restored.root_id != root_id:
            raise ContractError("invalid_snapshot", "the saved workspace is not the original workspace")
        restored.before = before
        return restored

    def _observe(self, path: str) -> tuple | None:
        """What is at the path now: None (nothing), ('other',) (not a readable regular file), or (identity, digest)."""
        try:
            ident, data = _read(self.root, self.root_id, path)
        except _Failed as failure:
            if failure.reason == "output_missing":
                return None
            return (failure.ident, None) if failure.ident is not None else ("other",)
        return (ident, hashlib.sha256(data).hexdigest())

    def _check(self, output: Output, before: tuple | None) -> tuple[str | None, str]:
        """(reason or None, one line about this output)."""
        try:
            ident, data = _read(self.root, self.root_id, output.path)
        except _Failed as failure:
            return failure.reason, str(failure)
        digest = hashlib.sha256(data).hexdigest()
        if before is None:
            how = "not there before the worker started"
        elif before == ("other",):
            how = "replaced what was there before the worker started"
        elif before[0] != ident or (before[1] is not None and before[1] != digest):
            how = "changed since the worker started"
        else:
            return "output_unchanged", ("the file is exactly as it was before the worker started (same content and "
                                        "timestamps): this run did not write it")
        problem = _format_problem(data, output)
        if problem:
            return "output_invalid", problem
        keys = f", keys {', '.join(output.required_keys)} present" if output.required_keys else ""
        return None, f"verified ({output.format}{keys}; {len(data):,} bytes; sha256 {digest}; {how})"

    def finish(self, limit: int = MAX_REPORT_CHARS) -> tuple[bool, str, str]:
        """(passed, the report for the lead, a row text with no path or content for the activity trace). The report
        is at most `limit` characters (unless its first line alone is longer): its first line is the verdict, and
        the per-output lines are shortened, then cut to the failed ones, then left out, before the verdict is."""
        results = [self._check(output, before) for output, before in zip(self.outputs, self.before)]
        failures = [(index, reason) for index, (reason, _) in enumerate(results, 1) if reason is not None]
        total = len(results)
        if failures:
            index, reason = failures[0]
            head = (f"[Dream output check failed: reason={reason}] {len(failures)} of {total} declared output(s) "
                    "did not pass, so this task is NOT complete, whatever the worker's reply says.")
            tail = ("Dream did not send the worker back. Decide what to do: send the task again, do it yourself, or "
                    "report the failure.")
            row = (f"Declared output check failed: reason={reason} (output {index} of {total}); "
                   "the result was returned to the lead agent as failed.")
        else:
            head = f"[Dream output check: {total} declared output(s) verified]"
            tail = ("Checked just now, by reading and never running: each is a regular file inside the workspace, "
                    "reached through no symbolic link, within the size limit, different from what was there before "
                    "the worker started, and passes its format check; the sha256 is of the bytes checked. Not "
                    "checked: who wrote it (anything that can write the workspace could have, not only this "
                    "worker), whether its content is right, or that it stays unchanged after this check.")
            row = ""
        full = [f"- {output.path}: " + (text if reason is None else f"reason={reason} -- {text}")
                for output, (reason, text) in zip(self.outputs, results)]
        short = [f"- output {index} of {total}, {_short(output.path)}: " + ("verified" if reason is None else f"reason={reason}")
                 for index, (output, (reason, _)) in enumerate(zip(self.outputs, results), 1)]
        failed_only = [line for line, (reason, _) in zip(short, results) if reason is not None]
        for lines, note in (
                (full, None),
                (short, "(Lines shortened to fit: position in `outputs`, the path with its middle cut if long, the "
                        "result; a verified file's size and sha256 are not repeated.)"),
                (failed_only, "(To fit, only the outputs that did not pass are listed, shortened the same way.)"),
                ([], "(The per-output lines were left out to fit; the count above stands.)")):
            report = "\n".join([head] + lines + ([note] if note else []) + [tail])
            if len(report) <= limit:
                return not failures, report, row
        return not failures, head, row


def _short(path: str, keep: int = 72) -> str:
    return path if len(path) <= keep else f"{path[:keep // 2 - 2]}...{path[-(keep // 2 - 1):]}"


def _bounded(text: str, room: int) -> str:
    """`text` in at most `room` characters: whole when it fits, else its start and its end around a note."""
    if len(text) <= room:
        return text
    note = (f"\n[Dream: the middle of this {len(text):,}-character worker reply was left out so that the output "
            "check stays in view]\n")
    keep = room - len(note)
    if keep < 80:
        note = f"[Dream: the worker's {len(text):,}-character reply was left out to fit]"
        return note if len(note) <= room else ""
    head = keep * 2 // 3
    return text[:head] + note + text[len(text) - (keep - head):]


def with_report(reply: str, report: str, passed: bool, cap: int) -> str:
    """The task result the lead gets: the worker's reply and Dream's report of the output check, in at most `cap`
    characters (or the report alone when it is longer), so a later prefix cut of the result cannot drop the report.
    A failed check comes FIRST, the reply after it and bounded: however long or confident the reply, the result
    starts with the verdict. A passed check keeps the reply first -- unchanged when both fit, its middle cut when
    not -- and the report whole after it."""
    if passed:
        kept = _bounded(reply, max(cap - len(report) - 2, 0))
        return f"{kept}\n\n{report}" if kept else report
    label = "\n\nThe worker's own reply follows; Dream confirmed none of it:\n"
    kept = _bounded(reply, max(cap - len(report) - len(label), 0))
    return report + label + kept if kept else report
