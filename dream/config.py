"""Central configuration for Dream: filesystem layout, service endpoints, knobs.

Everything is derived from the package location so Dream is relocatable, and every
value can be overridden with a ``DREAM_*`` environment variable so the agent can
retune its own home without editing code.
"""

from __future__ import annotations

import glob
import os
from pathlib import Path

from .environment import read_numeric

# --- Filesystem layout -------------------------------------------------------

PKG_DIR = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("DREAM_ROOT", str(PKG_DIR.parent))).resolve()

DATA_DIR = ROOT / "data"
SESSIONS_DIR = DATA_DIR / "sessions"
DB_PATH = Path(os.environ.get("DREAM_DB", str(DATA_DIR / "dream.db")))

MEMORY_DIR = ROOT / "memory"
SEMANTIC_DIR = MEMORY_DIR / "semantic"
PROCEDURAL_DIR = MEMORY_DIR / "procedural"
EPISODIC_DIR = MEMORY_DIR / "episodic"
IDENTITY_FILE = MEMORY_DIR / "IDENTITY.md"
THREADS_FILE = MEMORY_DIR / "THREADS.md"
# The index loaded into every wake-up: one line per memory, regenerated on write.
MEMORY_INDEX_FILE = MEMORY_DIR / "MEMORY.md"
# The user's standing preferences, injected into every system prompt (see /instructions).
INSTRUCTIONS_FILE = MEMORY_DIR / "INSTRUCTIONS.md"

# Uppercase files under memory/ that are NOT memories — a memory named after one
# of these is suffixed rather than overwriting it.
RESERVED_MEMORY_FILES = frozenset({"MEMORY.md", "IDENTITY.md", "THREADS.md", "INSTRUCTIONS.md"})


def reserved_memory_name(name: str) -> bool:
    """Case-insensitive: a memory slugifies to lowercase, and `identity.md` beside
    `IDENTITY.md` would be one file on a case-folding disk and a trap on any."""
    return f"{name}.md".upper() in {r.upper() for r in RESERVED_MEMORY_FILES}


def free_memory_name(name: str) -> str:
    """A reserved stem gets a suffix rather than a silent drop — the same `-2`
    the migration gives a collision, so every writer lands on the same file."""
    return f"{name}-2" if name and reserved_memory_name(name) else name

# Per-workspace project instructions, read like Claude Code reads CLAUDE.md.
PROJECT_INSTRUCTION_FILES = ("DREAM.md", "CLAUDE.md")
PROJECT_INSTRUCTIONS_MAX = 32_768  # bytes; a longer file is truncated with a note

# A memory file past this many characters gets a near-cap note telling the model
# to consolidate rather than shave.
MEMORY_FILE_MAX = read_numeric("DREAM_MEMORY_FILE_MAX")

VAR_DIR = ROOT / "var"
LOG_DIR = VAR_DIR / "logs"
LOOP_DIR = VAR_DIR / "loops"  # per-run autonomous-loop workspaces (state on disk)
CUSTOM_TOOLS_DIR = PKG_DIR / "tools" / "custom"
# Plugins: one directory each under here, a plugin.yaml plus any of skills/,
# tools/, agents/, mcp.json (dream/plugins.py).
PLUGINS_DIR = Path(os.environ.get("DREAM_PLUGINS_DIR", str(ROOT / "plugins")))

# The Library: durable, versioned, id-addressed files. Separate from the memory
# database because it holds user-facing deliverables rather than Dream's own
# recollection, and because its blobs are content-addressed files, not rows.
LIBRARY_DIR = DATA_DIR / "library"
LIBRARY_DB = Path(os.environ.get("DREAM_LIBRARY_DB", str(LIBRARY_DIR / "library.db")))
LIBRARY_BLOBS = LIBRARY_DIR / "blobs"

# External MCP servers Dream consumes (the inbound direction; dream.mcp is the
# outbound one). {"servers": [{name, command, args, env}]} — the same shape Dream
# emits when registering itself into a CLI agent, so one vocabulary covers both.
MCP_CONFIG_PATH = Path(os.environ.get("DREAM_MCP_CONFIG", str(ROOT / "mcp.json")))

# Dream's compact workflow catalog. External clients' catalogs are opt-in and
# remain searchable rather than being inserted wholesale into model context.
CURATED_SKILLS = ('coding', 'research', 'writing', 'documents', 'data-analysis',
                  'media', 'library', 'verifying', 'computer-use', 'blender-animation',
                  'brainstorming', 'debugging', 'gated-build', 'frontend-design',
                  'grill-me', 'handoff')


def bundled_skill_dirs() -> list[Path]:
    """Curated package paths in checkout/wheel precedence order."""
    roots = dict.fromkeys((ROOT / 'skills', PKG_DIR.parent / 'skills', PKG_DIR / 'resources' / 'skills'))
    return [root / name for root in roots for name in CURATED_SKILLS]


SKILL_DIR_PATTERNS = [str(path) for path in bundled_skill_dirs()]


def skill_dirs() -> list[Path]:
    """Default curated roots; external catalogs require DREAM_EXTERNAL_SKILLS=1.

    DREAM_SKILL_DIRS replaces the curated/external file roots; an empty value
    disables those roots. Private editor saves remain first in precedence.
    Explicitly enabled Dream plugins remain available
    through their own setting. Discovery never modifies external packages.
    """
    override = os.environ.get('DREAM_SKILL_DIRS')
    patterns = override.split(os.pathsep) if override is not None else list(SKILL_DIR_PATTERNS)
    if override is None and os.environ.get('DREAM_EXTERNAL_SKILLS') == '1':
        from .skills.catalog import plugin_skill_dirs
        codex = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).expanduser()
        patterns.extend([str(codex / 'skills'), str(Path.home() / '.agents' / 'skills')])
        patterns.extend(str(root) for root in plugin_skill_dirs())
    # Dream plugins have their own explicit enable setting. Respect that without
    # pulling in arbitrary cached plugins from other clients.
    from . import plugins
    patterns.extend(str(root) for root in plugins.skill_dirs())
    # Explicit editor saves are private overrides, ahead of imported packages.
    patterns.insert(0, str(DATA_DIR / 'skills'))
    out, seen = [], set()
    for pattern in patterns:
        pattern = pattern.strip()
        if not pattern:
            continue
        matches = sorted(glob.glob(os.path.expanduser(pattern))) if any(
            c in pattern for c in '*?[') else [os.path.expanduser(pattern)]
        for match in matches:
            path = Path(match)
            try:
                key = path.resolve()
                if not path.is_dir() or key in seen:
                    continue
            except OSError:
                continue
            seen.add(key)
            out.append(path)
    return out

# --- Model / engine ----------------------------------------------------------

# Model alias understood by the Claude Code CLI. None = CLI default.
MODEL = os.environ.get("DREAM_MODEL") or None
# SDK permission mode. "default" routes Write/Edit/Bash through Dream's own
# permission policy (mode cycle + workspace boundary in the TUI) — the accept-edits
# behavior lives there. Legacy metadata only: the SDK adapter pins "default"
# so an environment override cannot silently bypass Dream's current mode.
PERMISSION_MODE = os.environ.get("DREAM_PERMISSION_MODE", "default")

# Semantic (vector) memory via a local CPU embedding model. Falls back to keyword
# recall automatically if disabled or if fastembed isn't available.
# SIDELINED by default (2026-07-10): the vector stack (embeddings + reranker) and
# the agentic exit-consolidation were heavyweight for a young corpus and weren't
# earning their keep. Memory still works via keyword/FTS + the markdown mirror.
# Re-enable the vector layer with DREAM_SEMANTIC_MEMORY=1 (and DREAM_RERANK=1).
SEMANTIC_MEMORY = os.environ.get("DREAM_SEMANTIC_MEMORY", "0") == "1"
EMBED_MODEL = os.environ.get("DREAM_EMBED_MODEL", "BAAI/bge-small-en-v1.5")  # or bge-large-en-v1.5

# Cross-encoder reranker over the top hybrid candidates (biggest precision win).
RERANK = os.environ.get("DREAM_RERANK", "0") == "1"
RERANK_MODEL = os.environ.get("DREAM_RERANK_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2")
RERANK_CANDIDATES = read_numeric("DREAM_RERANK_CANDIDATES")

# End-of-session consolidation (the agentic promote/reconcile/summarize pass).
# SIDELINED by default (2026-07-10): it was slow, prompt-y, and memorialized
# session flailing without a quality gate. Re-enable with DREAM_CONSOLIDATE=1.
# `--no-consolidate` still forces it off regardless.
CONSOLIDATE_ON_EXIT = os.environ.get("DREAM_CONSOLIDATE", "0") == "1"

# Wake-up ranking: memories fade if unused (recency-weighted salience).
SALIENCE_HALFLIFE_DAYS = read_numeric("DREAM_SALIENCE_HALFLIFE")

# True ANN (HNSW) kicks in above this many vectors; below it, exact brute-force.
ANN_THRESHOLD = read_numeric("DREAM_ANN_THRESHOLD")

# Long memories are chunked so retrieval can match a passage.
CHUNK_THRESHOLD = read_numeric("DREAM_CHUNK_THRESHOLD")
CHUNK_SIZE = read_numeric("DREAM_CHUNK_SIZE")

# Consolidation merges near-duplicate memories above this cosine.
MERGE_THRESHOLD = read_numeric("DREAM_MERGE_THRESHOLD")

# Below MERGE but above RECONCILE, same-kind memories are "suspiciously similar":
# consolidation asks the model to adjudicate (merge / supersede / keep both).
# Each pair is asked about at most once, ever.
RECONCILE_THRESHOLD = read_numeric("DREAM_RECONCILE_THRESHOLD")
RECONCILE_MAX_CLUSTERS = read_numeric("DREAM_RECONCILE_MAX_CLUSTERS")
# Bodies longer than this are shown truncated to the adjudicator — and a group whose
# bodies weren't fully shown is never retired from future conflict checks.
RECONCILE_EXCERPT = read_numeric("DREAM_RECONCILE_EXCERPT")

# Recall reserves up to this many result slots for [[linked]] neighbors, so
# associations surface even when similarity fills the limit.
LINK_RESERVE = read_numeric("DREAM_LINK_RESERVE")

# New memories auto-link to nearest neighbors at/above this cosine (the association
# band; near-verbatim pairs above MERGE_THRESHOLD get merged instead).
AUTOLINK_THRESHOLD = read_numeric("DREAM_AUTOLINK_THRESHOLD")
AUTOLINK_K = read_numeric("DREAM_AUTOLINK_K")

# --- SearXNG -----------------------------------------------------------------

SEARXNG_DIR = Path(os.environ.get("DREAM_SEARXNG_DIR", str(Path.home() / "searxng")))
SEARXNG_HOST = os.environ.get("DREAM_SEARXNG_HOST", "127.0.0.1")
SEARXNG_PORT = read_numeric("DREAM_SEARXNG_PORT")
SEARXNG_URL = f"http://{SEARXNG_HOST}:{SEARXNG_PORT}"
# Autostart uses this interpreter (searxng's own venv, or a mise/py3.11 env).
SEARXNG_PYTHON = os.environ.get("DREAM_SEARXNG_PYTHON") or None
SEARXNG_AUTOSTART = os.environ.get("DREAM_SEARXNG_AUTOSTART", "1") == "1"

# --- Browser (Camoufox) ------------------------------------------------------

BROWSER_HEADLESS = os.environ.get("DREAM_BROWSER_HEADLESS", "1") == "1"
BROWSER_TIMEOUT_MS = read_numeric("DREAM_BROWSER_TIMEOUT_MS")
# Idle seconds before the persistent browser shuts itself down to save memory.
BROWSER_IDLE_SHUTDOWN_S = read_numeric("DREAM_BROWSER_IDLE_S")
SCREENSHOT_DIR = VAR_DIR / "screenshots"

# Assumed context window for hosted (Anthropic) models, used by the per-turn stats
# line when the backend can't report its own. Local backends probe theirs.
CONTEXT_WINDOW = read_numeric("DREAM_CTX_WINDOW")

# The right-side monitor pane (GPU telemetry + live inference speed). On by
# default; /monitor toggles it per session, DREAM_MONITOR=0 disables at boot.
MONITOR = os.environ.get("DREAM_MONITOR", "1") == "1"

# Dream Studio — the browser pane beside the terminal. Off unless `dream --gui`
# (or DREAM_GUI=1) asks for it. Port 0 lets the OS pick a free one, which keeps
# two concurrent Dreams from fighting over an address; set DREAM_GUI_PORT to pin
# it when you want a stable bookmark. The server binds loopback ALWAYS — that is
# not configurable, because the session behind it can run shell commands.
GUI = os.environ.get("DREAM_GUI", "0") == "1"
GUI_PORT = read_numeric("DREAM_GUI_PORT")
GUI_OPEN = os.environ.get("DREAM_GUI_OPEN", "1") == "1"

# A self-built tool unused for this many days gets a STALE tag in its description —
# a nudge to rebuild-or-retire instead of reaching for it out of convenience.
TOOL_STALE_DAYS = read_numeric("DREAM_TOOL_STALE_DAYS")

# Per-prompt tool-call budget for local/CLI backends. UNLIMITED by default — the
# loop guard (identical call → identical result) is the real runaway protection,
# so a legitimate long research turn is never artificially cut off. Set
# DREAM_TOOL_BUDGET to a number to cap it; /toolcalls overrides per session.
DEFAULT_TOOL_BUDGET_LOCAL = read_numeric("DREAM_TOOL_BUDGET")

# Ceiling on tokens per single local-model generation (OpenAI-compat backend).
# Bounded only so a token-level loop self-terminates — the backend clamps this
# to what the context window can still hold anyway (see _max_tokens), so the
# real limit on a small-window session is the window, not this number.
#
# Was 32768, which is ~130 KB and predates the big-context local models: on a
# 250k-window session it capped a single generation at 13% of the window, and
# one write_file of a full HTML page (inline CSS + JS) exceeded it — the turn
# died mid-tool-call having written nothing. A runaway is already caught by the
# loop guard and the per-prompt tool budget; truncating real work is the worse
# failure, so this is now generous and the window does the bounding.
MAX_OUTPUT_TOKENS = read_numeric("DREAM_MAX_TOKENS")

# How many tool rounds a dispatched subagent may run before it must wrap up.
# High by default so real research completes; the main loop's guard still catches
# an identical-call spiral.
SUBAGENT_MAX_ROUNDS = read_numeric("DREAM_SUBAGENT_ROUNDS")

# Max tool ROUNDS (model generations) in one lead turn. High backstop — the loop
# guard is what actually stops a spiral; this just prevents a truly infinite turn.
MAX_TOOL_ROUNDS = read_numeric("DREAM_MAX_TOOL_ROUNDS")
TOOL_RESULT_CAP = int(os.environ.get("DREAM_TOOL_RESULT_CAP", "24000"))

# Anti-loop sampling for local models (the max_tokens cap is the guaranteed stop; these
# keep the model from entering a repetition loop in the FIRST place). Gentle defaults —
# code legitimately repeats, so heavy penalties break it. Set to 0 (or 1.0 for
# repetition) to disable. Tune up if a model still loops, down if code quality suffers.
FREQUENCY_PENALTY = read_numeric("DREAM_FREQUENCY_PENALTY")
PRESENCE_PENALTY = read_numeric("DREAM_PRESENCE_PENALTY")
REPETITION_PENALTY = read_numeric("DREAM_REPETITION_PENALTY")

# --- Timeouts ----------------------------------------------------------------

HTTP_TIMEOUT_S = read_numeric("DREAM_HTTP_TIMEOUT_S")

# Read timeout for a local-model generation. NO TIMEOUT by default — a local 35B
# can take as long as it takes on a big output, and the user's Ctrl-C (plus
# MAX_OUTPUT_TOKENS) is the stop. Set DREAM_LLM_READ_TIMEOUT_S to a number of
# seconds to re-impose a cap.
LLM_READ_TIMEOUT_S = read_numeric("DREAM_LLM_READ_TIMEOUT_S")

# MCP server name; tools are exposed to the model as ``mcp__dream__<tool>``.
MCP_SERVER_NAME = "dream"


def tool_id(name: str) -> str:
    """Fully-qualified tool name as the model sees it."""
    return f"mcp__{MCP_SERVER_NAME}__{name}"


def ensure_dirs() -> None:
    """Create every directory Dream writes to. Safe to call repeatedly."""
    for d in (
        DATA_DIR,
        SESSIONS_DIR,
        MEMORY_DIR,
        SEMANTIC_DIR,
        PROCEDURAL_DIR,
        EPISODIC_DIR,
        VAR_DIR,
        LOG_DIR,
        LOOP_DIR,
        SCREENSHOT_DIR,
        CUSTOM_TOOLS_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)
