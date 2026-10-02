"""The frontier benchmark harness (scripts/frontier_benchmark.py): what it spends before it spends it, where it keeps
Dream's state, and its whole pipeline on the dry run -- runs, blind judging, the report. No model, no network: the
script runs as a subprocess, because it must set Dream's paths before Dream is imported. The gate's round-1 cases
(leases, blind judging, judging without consent, the tools) run the script under an audit hook that refuses what the
case must not do -- a connection, a process, the owner's live lease folder -- so a broken build fails without doing
it."""
import asyncio
import itertools
import json
import os
import re
import runpy
import stat
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from lease_isolation import REAL_LEASE_DIR

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "frontier_benchmark.py"
TASKS = ("crosscheck", "docs-vs-code", "plan-change")

# The built-in tasks name files of the Dream checkout (docs/ among them); a checkout without them cannot plan or run.
_TASK_FILES_ABSENT = sorted({path for task in runpy.run_path(str(SCRIPT))["TASKS"] for path in task["files"]
                             if not (SCRIPT.parents[1] / path).is_file()})
needs_task_files = pytest.mark.skipif(bool(_TASK_FILES_ABSENT),
                                      reason=f"the benchmark's built-in tasks name files this checkout does not carry: "
                                             f"{', '.join(_TASK_FILES_ABSENT)}")


def bench(*args, cwd):
    done = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=300, cwd=cwd)
    return done.returncode, done.stdout + done.stderr


# The audit hook a child runs the script under: every connection, process start and file access it makes is logged,
# and what the case forbids is refused before it happens.
AUDIT = r'''
import json, os, runpy, sys
log = os.open(os.environ["AUDIT_LOG"], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
writable = tuple(p for p in os.environ["AUDIT_WRITABLE"].split(os.pathsep) if p)
forbidden = tuple(p for p in os.environ.get("AUDIT_FORBIDDEN", "").split(os.pathsep) if p)
ports = {int(p) for p in os.environ.get("AUDIT_PORTS", "").split(",") if p}
spawns = os.environ.get("AUDIT_SPAWNS") == "allow"
SPAWN = {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.fork", "os.forkpty", "os.spawn", "pty.spawn"}
CHANGE = {"os.mkdir", "os.rename", "os.remove", "os.rmdir", "os.symlink", "os.link", "os.chmod", "os.chown",
          "os.truncate", "os.utime", "shutil.copyfile", "shutil.rmtree", "shutil.move"}
LOOK = {"os.listdir", "os.scandir"}
WRITING = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC

def note(*item):
    os.write(log, (json.dumps(item, default=repr) + "\n").encode())

def under(path, roots):
    return any(path == root or path.startswith(root.rstrip("/") + "/") for root in roots)

def check(event, name, changing, folder=False):
    if not isinstance(name, (str, bytes, os.PathLike)):
        return                                               # a descriptor, a mode, a time
    path = os.fsdecode(name)
    if not os.path.isabs(path):
        if changing:
            note("relative", event, path)                    # under a folder opened by its absolute path
        return
    if under(path, forbidden):
        note("refused", event, path)
        raise PermissionError(f"audit: {path} is not this run's")
    if folder:
        note("folder", event, path)
    elif changing:
        note("write" if under(path, writable) else "outside", event, path)

def hook(event, args):
    if event == "socket.connect":
        address = args[1]
        port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
        note("connect", repr(address), port in ports)
        if port not in ports:
            raise PermissionError(f"audit: no connection to {address!r}")
    elif event in SPAWN:
        note("spawn", event, repr(args)[:300])
        if not spawns:
            raise PermissionError("audit: no process start")
    elif event == "open":
        path, mode, flags = args
        check(event, path, (isinstance(mode, str) and any(c in mode for c in "wax+"))
              or (isinstance(flags, int) and bool(flags & WRITING)), isinstance(flags, int) and bool(flags & os.O_DIRECTORY))
    elif event in ("shutil.copyfile", "os.link", "os.symlink"):
        check(event, args[0], False)                         # read
        check(event, args[1], True)                          # made
    elif event in CHANGE:
        for name in args[:2]:
            check(event, name, True)
    elif event in LOOK:
        check(event, args[0], False)
sys.addaudithook(hook)
script = os.environ["AUDIT_SCRIPT"]
sys.argv = [script] + sys.argv[1:]
runpy.run_path(script, run_name="__main__")
'''


def audited(tmp_path, name, *args, env=None, forbidden=(), ports=(), spawns=False):
    """The script run with `args` under the audit hook -> (exit code, output, events). Writes may go under tmp_path;
    `forbidden` folders are refused outright (a look included); connections only to `ports`; processes only with
    `spawns`."""
    log = tmp_path / f"audit-{name}.jsonl"
    child = dict(os.environ if env is None else env)
    child.update(AUDIT_LOG=str(log), AUDIT_SCRIPT=str(SCRIPT), AUDIT_WRITABLE=os.pathsep.join([str(tmp_path), "/dev/null"]),
                 AUDIT_FORBIDDEN=os.pathsep.join(str(p) for p in forbidden),
                 AUDIT_PORTS=",".join(str(p) for p in ports), AUDIT_SPAWNS="allow" if spawns else "refuse",
                 TMPDIR=str(tmp_path))
    done = subprocess.run([sys.executable, "-c", AUDIT, *args], capture_output=True, text=True, timeout=600,
                          cwd=tmp_path, env=child)
    events = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return done.returncode, done.stdout + done.stderr, events


def spending(events):
    """What costs or reaches past the run: connections, process starts, writes outside its folder, refusals."""
    return [e for e in events if e[0] in ("connect", "spawn", "outside", "refused")]


@needs_task_files
def test_the_estimate_comes_first_and_nothing_runs_without_consent(tmp_path):
    """Before anything runs it prints the Opus estimate; without --yes (or --dry-run) it stops there. The plan says what
    a live run needs: a local engine nothing else uses meanwhile, and a checkout that is not the live app's."""
    out = tmp_path / "out"
    code, text = bench("run", "--out", str(out), "--opus-model", "opus", "--local-model", "qwen", cwd=tmp_path)
    assert code == 2 and text.startswith("Opus estimate: 6 Opus runs, about ")
    assert text.index("Opus estimate") < text.index("Nothing was run: pass --yes")
    assert not (out / "runs").exists()
    code, text = bench("plan", "--judge-provider", "anthropic", cwd=tmp_path)
    assert code == 0 and all(f"task {task}:" in text for task in TASKS) and "Judge (anthropic): about " in text
    assert "a local engine nothing else uses while it runs" in text and "never the live app's checkout" in text


def test_every_dream_path_of_a_run_is_under_its_own_state_folder(tmp_path):
    """isolate() points Dream's root at DIR/state before Dream is imported: data, memory, sessions, logs, settings,
    plugins and the MCP config all derive from it, so a benchmark session never touches the owner's. The inference
    leases and the launch lock get private folders there too (gate round 1): resolved as a live run resolves them --
    no pytest in the environment, and override folders inherited from the parent -- never the owner's live
    /tmp/dream-inference-<uid>, nor the inherited ones. Resolving makes nothing."""
    out = tmp_path / "out"
    probe = (f"import runpy, sys; bench = runpy.run_path({str(SCRIPT)!r}); bench['isolate'](__import__('pathlib').Path({str(out)!r}))\n"
             "from dream import config\n"
             "from dream.core import settings\n"
             "from dream.core.inference_coordination import lease_root\n"
             "from dream.local.load_lock import lock_root\n"
             "print('\\n'.join(str(p) for p in (config.ROOT, config.DATA_DIR, config.SESSIONS_DIR, config.DB_PATH, "
             "config.MEMORY_DIR, config.LOG_DIR, config.PLUGINS_DIR, config.MCP_CONFIG_PATH, config.LIBRARY_DB, "
             "lease_root(), lock_root())))\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    env.update(DREAM_INFERENCE_LEASE_DIR=str(tmp_path / "inherited-leases"),
               DREAM_MACHX_LOAD_LOCK_DIR=str(tmp_path / "inherited-locks"))
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=120, cwd=tmp_path,
                          env=env)
    assert done.returncode == 0, done.stderr[-2000:]
    state = (out / "state").resolve()
    paths = [Path(line) for line in done.stdout.split()]
    assert len(paths) == 11 and all(path == state or state in path.parents for path in paths), paths
    assert paths[-2:] == [state / "leases", state / "locks"]


def test_a_live_run_keeps_its_leases_under_its_state_folder(tmp_path):
    """(Gate round 1, finding 1) A consented live run -- configuration c against a fake local engine on loopback, no
    pytest in its environment, lease and lock overrides inherited from its parent -- takes its inference leases only
    in DIR/state/leases, a private folder by Dream's rules (mode 0700). The owner's live lease folder is refused to it
    outright, even a look, and nothing tried; the folder is unchanged."""
    from test_delegate_local import _LocalEngine
    before = sorted(os.listdir(REAL_LEASE_DIR)) if REAL_LEASE_DIR.is_dir() else None
    engine = _LocalEngine(models=("qwen-local",), slots=4, hold=0.0)
    port = int(engine.url.split(":")[2].split("/")[0])
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}          # a live run
    env.update(DREAM_INFERENCE_LEASE_DIR=str(tmp_path / "inherited-leases"),
               DREAM_MACHX_LOAD_LOCK_DIR=str(tmp_path / "inherited-locks"),
               DREAM_MACHX_URL=engine.url, DREAM_MACHX_HOST="127.0.0.1", DREAM_MACHX_PORT=str(port))
    out = tmp_path / "out"
    try:
        code, text, events = audited(tmp_path, "live-c", "run", "--out", str(out), "--configs", "c", "--tasks",
                                     "plan-change", "--local-model", "qwen-local", "--timeout-s", "120", "--yes",
                                     env=env, forbidden=[REAL_LEASE_DIR], ports=[port], spawns=True)
        chats = engine.chats
    finally:
        engine.close()
    record = json.loads((out / "runs" / "plan-change__c" / "record.json").read_text())
    assert code == 0 and record["outcome"] == "completed" and chats >= 1, text[-2000:]
    assert record["tools"] == ["grep", "list_dir", "read_file"]             # what the real Engine offered (gate: 4)
    # what its first request offered, read from the payload: no verifier (gate round 2, finding 2)
    assert record["first_request_tools"] == ["grep", "list_dir", "read_file", "task"]
    leases = (out / "state" / "leases").resolve()
    assert [e for e in events if e[0] == "refused"] == []
    assert ["folder", "open", str(leases)] in events                         # the lease records' folder
    assert any(e[0] == "relative" and e[2].endswith((".json", ".lock", ".tmp")) for e in events)   # records in it
    assert stat.S_IMODE(leases.stat().st_mode) == 0o700
    assert not (tmp_path / "inherited-leases").exists() and not (tmp_path / "inherited-locks").exists()
    assert (sorted(os.listdir(REAL_LEASE_DIR)) if REAL_LEASE_DIR.is_dir() else None) == before


def test_a_run_offered_more_than_the_file_readers_asks_nothing(tmp_path):
    """(Gate round 1, finding 4; round 2, finding 2) A tool past the file readers that the run's switch-off does not
    cover -- here the checkout's own custom tool, preview_tui, trusted in the run's settings beforehand, as an owner
    would trust it -- stops the run before its model is asked: the local lead's first request offers it, so the
    request is never sent; the record names the tool, and the engine gets no request."""
    from test_delegate_local import _LocalEngine
    out = tmp_path / "out"
    source = SCRIPT.parents[1] / "dream" / "tools" / "custom" / "preview_tui.py"
    trust = ("import hashlib; from dream import extensions; extensions.trust_module('tool:custom/preview_tui', "
             f"hashlib.sha256(open({str(source)!r}, 'rb').read()).hexdigest())")
    subprocess.run([sys.executable, "-c", trust], check=True, capture_output=True, timeout=120, cwd=tmp_path,
                   env=dict(os.environ, DREAM_ROOT=str(out / "state"),
                            DREAM_EXTENSION_SETTINGS=str(out / "state" / "extensions.json")))
    engine = _LocalEngine(models=("qwen-local",), slots=4, hold=0.0)
    port = int(engine.url.split(":")[2].split("/")[0])
    env = dict(os.environ, DREAM_MACHX_URL=engine.url, DREAM_MACHX_HOST="127.0.0.1", DREAM_MACHX_PORT=str(port))
    try:
        code, text, events = audited(tmp_path, "offered", "run", "--out", str(out), "--configs", "c", "--tasks",
                                     "plan-change", "--local-model", "qwen-local", "--timeout-s", "120", "--yes",
                                     env=env, forbidden=[REAL_LEASE_DIR], ports=[port], spawns=True)
        chats = engine.chats
    finally:
        engine.close()
    record = json.loads((out / "runs" / "plan-change__c" / "record.json").read_text())
    assert record["outcome"] == ("error: the first request offered tools past the file readers (preview_tui); "
                                 "nothing was asked"), text[-2000:]
    assert chats == 0 and record["first_request_tools"] == ["grep", "list_dir", "preview_tui", "read_file", "task"]


def test_a_first_request_offering_the_verifier_is_never_sent():
    """(Gate round 2, finding 2) The local lead's guard reads the tools its first chat request offers -- the payload,
    not the registry, which never listed fork_verifier_agent: that request is stopped before it is sent when it offers
    the verifier, or any tool past the file readers and task, and so is every chat request after it. The first
    request's tools are kept for the record; other requests to the engine pass."""
    bench = runpy.run_path(str(SCRIPT))

    def chat(*names):
        return httpx.Request("POST", "http://127.0.0.1:9/v1/chat/completions", json={
            "model": "m", "tools": [{"type": "function", "function": {"name": name}} for name in names]})
    guard = bench["_FirstRequestGuard"](bench["FILE_READERS"] | {"task"})
    asyncio.run(guard(httpx.Request("GET", "http://127.0.0.1:9/v1/models")))
    with pytest.raises(RuntimeError, match=r"\(fork_verifier_agent\)"):
        asyncio.run(guard(chat("read_file", "task", "fork_verifier_agent")))
    with pytest.raises(RuntimeError):
        asyncio.run(guard(chat("read_file")))                               # nothing after it goes out either
    assert (guard.offered, guard.extra) == (["fork_verifier_agent", "read_file", "task"], ["fork_verifier_agent"])
    fine = bench["_FirstRequestGuard"](bench["FILE_READERS"] | {"task"})
    asyncio.run(fine(chat("grep", "list_dir", "read_file", "task")))
    assert (fine.offered, fine.extra) == (["grep", "list_dir", "read_file", "task"], [])


@needs_task_files
def test_judging_without_consent_spends_nothing(tmp_path):
    """(Gate round 1, finding 3) `judge` without --yes prints the estimate and stops: no connection, no process (the
    Claude judge would start the CLI), no write outside the run's folder, no judge folder. The dry run that makes its
    runs makes none either."""
    out = tmp_path / "out"
    code, text, events = audited(tmp_path, "dry", "run", "--out", str(out), "--dry-run")
    assert code == 0 and spending(events) == [], (text[-1500:], spending(events))
    code, text, events = audited(tmp_path, "judge", "judge", "--out", str(out), "--judge-provider", "anthropic",
                                 "--judge-model", "opus")
    assert code == 2 and "Nothing was judged: pass --yes" in text, text[-1500:]
    assert spending(events) == [] and not (out / "judge").exists()


def test_only_the_file_readers_are_offered_in_every_configuration(tmp_path):
    """(Gate round 1, finding 4) Each run's tools: the file readers (reading, listing, searching the task's workspace),
    plus delegate_local for (b) and the task tool for (c). Every other tool Dream ships is switched off in the run's own
    Extensions settings -- the read-only ones included, which Dream pre-approves without asking the permission
    callback -- so web_search and the GitHub reads are neither offered nor run, in every configuration, and the
    callback refuses them too. (c)'s workers and (b)'s helpers get the session's tools and callback."""
    out = tmp_path / "out"
    probe = (f"import asyncio, json, runpy; bench = runpy.run_path({str(SCRIPT)!r}); bench['isolate'](__import__('pathlib').Path({str(out)!r}))\n"
             "from dream import extensions\n"
             "from dream.tools import registry, web, github_tools\n"
             "from dream.tools.native import NATIVE_TOOLS\n"
             "gh = next(t for t in github_tools.GITHUB_TOOLS if t.name == 'github_read_file')\n"
             "found = {}\n"
             "for config in 'abc':\n"
             "    bench['_tools_for'](config)\n"
             "    offered = [t.name for t in extensions.filter_tools(registry._BASE_TOOLS + NATIVE_TOOLS)]\n"
             "    ran = [asyncio.run(extensions.guard_tool(t).handler({'query': 'x', 'repo': 'o/r', 'path': 'p'})) for t in (web.web_search, gh)]\n"
             "    decide = bench['_permission'](config)\n"
             "    asked = {n: asyncio.run(decide(n, {})) for n in ('web_search', 'mcp__dream__web_search', 'github_read_file',\n"
             "             'mcp__dream__github_read_file', 'browse', 'Read', 'Grep', 'mcp__dream__read_file', 'mcp__dream__list_dir',\n"
             "             'mcp__dream__grep', 'mcp__dream__delegate_local', 'task', 'write_file', 'Agent')}\n"
             "    found[config] = {'offered': offered, 'ran': [r.get('is_error') for r in ran],\n"
             "                     'delegate_local': extensions.is_enabled('tool:delegate_local'), 'asked': asked}\n"
             "print(json.dumps(found))\n")
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=120, cwd=tmp_path)
    assert done.returncode == 0, done.stderr[-2000:]
    found = json.loads(done.stdout.splitlines()[-1])
    for config in "abc":
        seen = found[config]
        assert sorted(set(seen["offered"])) == ["grep", "list_dir", "read_file"], (config, seen["offered"])
        assert seen["ran"] == [True, True] and seen["delegate_local"] == (config == "b")
        allowed = {name for name, ok in seen["asked"].items() if ok}
        assert allowed == {"Read", "Grep", "mcp__dream__read_file", "mcp__dream__list_dir", "mcp__dream__grep",
                           *(["mcp__dream__delegate_local"] if config == "b" else []),
                           *(["task"] if config == "c" else [])}, (config, allowed)


# --- blind judging (gate round 1, finding 2) ---------------------------------------------------------------------

IDS = ("claude-opus-5-5", "qwen3.8-35b-a3b")        # the configured models: --opus-model, --local-model

# A correct answer's substantive lines, per task: the task's own vocabulary (workers, sub-agents, Claude, Nested,
# the local engine, MachX's code names) stays for the judge.
SUBSTANCE = {
    "crosscheck": [
        "### dream/core/inference_coordination.py",
        "- lease_root() keeps each process's inference leases: DREAM_INFERENCE_LEASE_DIR when set, else "
        "/tmp/dream-inference-<uid>, or a private folder under pytest.",
        "- A lease opens as many slots as the server reports (_slots_for, at most MAX_SLOTS); a waiting request says so "
        "on the local engine.",
        "### dream/local/load_lock.py",
        "- One local engine launch per user and port: load_lock() takes dream-machx-load-<uid>-<port>.lock under "
        "lock_root(), which DREAM_MACHX_LOAD_LOCK_DIR overrides; machx.serve holds it.",
        "### Cross-module checks",
        "- load_lock.lock_root() relies on inference_coordination._under_pytest(): holds.",
        "- conversation.py's reconnect history replays each Nested worker row that bounded_agent_activity() let "
        "through: holds.",
        "- The Claude sub-agent rows (anthropic.py) are bounded the same way: holds.",
    ],
    "docs-vs-code": [
        "1. docs/inference-coordination.md: \"The persistent lock inode lives in /tmp/dream-machx-load-UID-PORT.lock\". "
        "lock_root() honours DREAM_MACHX_LOAD_LOCK_DIR first; the doc says so two lines later, so both agree.",
        "2. docs/nested-dream.md: each of the orchestrator's calls \"is a lane on this page\". agent_activity.py's rows "
        "carry no lane number (bounded_agent_activity keeps run_id, agent, phase, round): the doc is wrong.",
        "3. docs/nested-dream.md: a Claude session's sub-agents are read-only cards. agent_activity.py does not know the "
        "provider, so it neither confirms nor contradicts it: no inconsistency.",
    ],
    "plan-change": [
        "- dream/agent_activity.py, bounded_agent_activity(): each Nested worker row gains elapsed_ms, the time since "
        "its run's first row. Test: a row keeps elapsed_ms through the bounds.",
        "- dream/gui/conversation.py, the reconnect history: keep elapsed_ms in the rows it replays. Test: a reconnect "
        "shows the same elapsed_ms.",
        "- dream/core/backends/anthropic.py, _SubagentCards._row(): the Claude sub-agent rows carry elapsed_ms from "
        "run[\"since\"]. Test: a scripted sub-agent's rows grow.",
        "- Risks: the workers' clocks are per process, so elapsed_ms does not compare across processes; old sessions' "
        "rows have none.",
    ],
}

# How each configuration narrates its work before the section: every string the gate's round 1 found in (b) and (c).
PREAMBLE = {
    "a": ["I read the files myself, one after another, and checked each claim against the code.",
          "claude-opus-5-5 noted where the docs and the code disagree."],
    "b": ["I split the work into four sub-tasks and ran them with delegate_local.",
          "[Dream] delegate_local ran 4 of 4 tasks on the local engine's qwen3.8-35b-a3b (4 lanes) in 212 s.",
          "### Task 1 · explorer · completed",
          "The helpers agreed on two issues.",
          "Worker 2 found the lease bug.",
          "Local model output was checked against the code."],
    "c": ["I sent four task calls in one reply; the workers came back with their findings.",
          "Via the local engine (MachX, mimo-v2.6-flash) each sub-agent read one module.",
          "I delegated the docs check to a worker and verified its claims."],
}

# What slips into the section anyway: each line must go, and only these.
SLIPS = {
    "a": ["Reviewed by claude-opus-5-5."],
    "b": ["### Task 2 · reviewer · completed",
          "[Dream] delegate_local ran 4 of 4 tasks on the local engine's qwen3.8-35b-a3b (4 lanes) in 212 s.",
          "Helper 3 confirmed the lock order.",
          "Worker 2 found the lease bug.",
          "The helpers agreed on two issues.",
          "I split the work into four sub-tasks and ran them with delegate_local.",
          "Local model output was checked against the code.",
          "qwen3.8-35b-a3b agreed on both points."],
    "c": ["I sent four task calls in one reply.",
          "Via the local engine (MachX, mimo-v2.6-flash) each sub-agent read one module.",
          "I delegated the docs check and verified its claims.",
          "The task tool returned four results.",
          "Split into two subtasks, then combined."],
}

# What would tell the judge the configuration (the kept vocabulary aside), and what would mark a removal.
TELLS = re.compile(r"delegate_local|helpers?\b|task calls?|task tool|sub-?tasks?|delegat|\[Dream\]|### Task \d|"
                   r"(?-i:\bMachX\b)|local model|mimo|qwen|claude-opus|worker \d", re.IGNORECASE)
MARKS = re.compile(r"\[…\]|\[\.\.\.\]|removed|redacted|…")


def _answer(task, config, section=True):
    """The narration, then (with `section`) the "## Answer" heading, then the substantive lines with the slips among
    them."""
    body = [line for pair in itertools.zip_longest(SUBSTANCE[task], SLIPS[config]) for line in pair if line is not None]
    return "\n".join(PREAMBLE[config]) + ("\n\n## Answer\n" if section else "\n\n") + "\n".join(body) + "\n"


def test_every_configuration_is_asked_for_the_same_answer_section():
    """(Gate round 1, finding 2a) Every configuration's prompt ends with the same instruction: a final section headed
    exactly "## Answer", with the answer only, nothing about how the work was done."""
    bench = runpy.run_path(str(SCRIPT))
    instruction = bench["ANSWER_INSTRUCTION"]
    assert '"## Answer"' in instruction and "nothing about how the work was done" in instruction
    for task in bench["TASKS"]:
        assert {bench["_prompt"](task, config).rsplit("\n\n", 1)[1] for config in "abc"} == {instruction}


def test_the_judge_sees_only_the_answer_without_process_lines():
    """(Gate round 1, finding 2b-2e) For each task and configuration, a realistic answer: its narration before the
    "## Answer" section (every string the gate found), then the section, with process lines slipped in. The judge sees
    the section only, without the lines that are process artifacts or narration, and nothing marks what went: exactly
    the substantive lines of the correct answer are left, in order -- "each Nested worker row" and "the Claude
    sub-agent rows" among them. Without the section, the judge sees the whole answer, those lines removed. How many
    lines went, and whether the section was there, is for the report, not the judge; its instructions say nothing of
    it."""
    bench = runpy.run_path(str(SCRIPT))
    judged_text = bench["_judged_text"]
    assert not MARKS.search(bench["JUDGE_SYSTEM"]) and "hide" not in bench["JUDGE_SYSTEM"]
    for task in TASKS:
        for config in "abc":
            text, extraction = judged_text(_answer(task, config), IDS)
            assert [line for line in text.split("\n") if line.strip()] == SUBSTANCE[task], (task, config, text)
            assert extraction == {"answer_section": True, "lines_removed": len(SLIPS[config])}, (task, config)
            assert not TELLS.search(text) and not MARKS.search(text), (task, config)
            whole, extraction = judged_text(_answer(task, config, section=False), IDS)
            assert extraction["answer_section"] is False and not TELLS.search(whole) and not MARKS.search(whole)
            assert [line for line in whole.split("\n") if line in SUBSTANCE[task]] == SUBSTANCE[task]
            packet = bench["_judge_prompt"](next(t for t in bench["TASKS"] if t["id"] == task), text)
            assert packet.split("<answer>\n", 1)[1].startswith(text) and "lines_removed" not in packet


def test_which_answer_section_the_judge_sees():
    """(Gate round 2, finding 3) Pinned, so a change is a choice: of a draft "## Answer" and a final one, the judge sees
    the final one; the section ends at the next heading of its level or above; and a heading that is not exactly
    "## Answer" -- "## Answer:", "### Answer", "## answer" -- is no section: the whole answer is judged, and the
    report says the section was missing."""
    judged_text = runpy.run_path(str(SCRIPT))["_judged_text"]
    drafted = ("Notes.\n\n## Answer\nA first draft.\n\n## Answer\nThe final answer.\n### Details\nKept.\n"
               "## After\nGone.\n")
    assert judged_text(drafted, IDS) == ("The final answer.\n### Details\nKept.",
                                         {"answer_section": True, "lines_removed": 0})
    for heading in ("## Answer:", "### Answer", "## answer"):
        answer = f"Notes.\n\n{heading}\nThe final answer.\n"
        assert judged_text(answer, IDS) == (answer.strip("\n"), {"answer_section": False, "lines_removed": 0}), heading


@needs_task_files
def test_the_dry_run_records_every_run_then_judges_blind_and_reports(tmp_path):
    """The dry run goes through the whole pipeline on stand-ins, with no connection or process: nine records with the
    wall time, Opus's and the local tokens and the answer; a blind judge packet per answer -- the three configurations'
    answers identical there, their process lines gone -- and one verdict each; a report that unblinds them and says,
    per answer, whether it had its section and how many lines the judge did not see."""
    out = tmp_path / "out"
    code, text, events = audited(tmp_path, "dry-run", "run", "--out", str(out), "--dry-run")
    assert code == 0 and text.startswith("Opus estimate:") and spending(events) == [], text
    records = [json.loads(path.read_text()) for path in sorted((out / "runs").glob("*/record.json"))]
    assert {(record["task"], record["config"]) for record in records} == {(t, c) for t in TASKS for c in "abc"}
    for record in records:
        assert record["outcome"] == "completed" and record["dry_run"] and isinstance(record["wall_s"], float)
        assert (record["opus"] is None) == (record["config"] == "c")
        assert (record["local"] is None) == (record["config"] == "a")
        assert (out / "runs" / f"{record['task']}__{record['config']}" / "output.md").read_text().startswith("[dry run]")
    helpers = next(record for record in records if record["config"] == "b")
    assert helpers["local"] == {"prompt_tokens": 3600, "completion_tokens": 1200, "source": "worker rows"}
    assert helpers["workers"] == 4
    local = next(record for record in records if record["config"] == "c")
    assert local["local"] == {"prompt_tokens": 4800, "completion_tokens": 1500,
                              "source": "local lead (workers included)"}

    code, text, events = audited(tmp_path, "dry-judge", "judge", "--out", str(out), "--dry-run")
    assert code == 0 and spending(events) == [], text
    key = json.loads((out / "judge" / "key.json").read_text())
    assert set(key) == set(TASKS) and all(sorted(labels.values()) == ["a", "b", "c"] for labels in key.values())
    assert any(labels != {"A": "a", "B": "b", "C": "c"} for labels in key.values())      # shuffled, per task
    for task in TASKS:
        answers = set()
        for label in ("A", "B", "C"):
            packet = (out / "judge" / task / f"{label}.md").read_text()
            answer = packet.split("<answer>", 1)[1].split("</answer>", 1)[0]
            assert not TELLS.search(answer) and not MARKS.search(answer) and "configuration" not in answer
            assert "The rubric:" in packet and "correctness:" in packet
            answers.add(answer)
            verdict = json.loads((out / "judge" / task / f"{label}.json").read_text())["verdict"]
            assert set(verdict["scores"]) == {"correctness", "coverage", "specificity", "usefulness"}
            assert verdict["total"] == sum(verdict["scores"].values())
        assert len(answers) == 1

    code, text = bench("report", "--out", str(out), cwd=tmp_path)
    assert code == 0
    rows = [line for line in (out / "report.md").read_text().splitlines()[2:] if line.startswith("|")]
    assert len(rows) == 9 and all(re.match(r"\| \S+ \| [abc] \(.+\) \| \d+ \|", row) for row in rows)
    report = json.loads((out / "report.json").read_text())
    for row in report:
        assert row["blind_label"] in ("A", "B", "C") and key[row["task"]][row["blind_label"]] == row["config"]
        assert (row["answer_section"], row["lines_removed"]) == {"a": (True, 0), "b": (True, 1), "c": (False, 1)}[row["config"]]
