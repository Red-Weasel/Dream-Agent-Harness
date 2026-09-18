# Development and validation

Use Python 3.12+ and the locked environment:

```bash
uv sync --locked --extra dev
uv run --locked pytest -q
```

Tests use disposable state. Model-loading checks are opt-in with `--with-models`;
inspect available resources before enabling them. Browser/native fixtures require
the corresponding browser binaries, native libraries, a graphical session where
applicable, and permission to create local sockets/subprocesses. A skip is not a pass.

For packaging:

```bash
uv build --wheel --out-dir /tmp/dream-wheel
uv run --locked python scripts/audit_distribution.py /tmp/dream-wheel
```

The layout audit checks runtime exclusions and required assets; it does not certify
all source content or historical commits free of sensitive information.

## Architecture

- `dream/core/`: engine, adapters, execution policies, request coordination, runs.
- `dream/gui/`: authenticated loopback companion, Chat/Studio, browser assets.
- `dream/desktop/`: GTK/VTE/WebKit shell and startup selection.
- `dream/tools/`: tools and their registration/trust boundaries.
- `dream/memory/`: memory persistence, retrieval, and consolidation implementation.
- `dream/media/`: media projects, jobs, history, and presentation.
- `dream/tui/`: terminal interaction and Council controls.
- `skills/`: bundled, reusable task workflows.
- `tests/` and `eval/`: tests and synthetic evaluation fixtures.

## Evidence and limits

The September 14 final CPU candidate passed **5,179 tests**, with **45 skipped**,
**one explicitly deselected**, **7 warnings** and **52 passing subtests**, in
480.15 seconds. Skips were 34 opt-in model tests and 11 GTK/GI tests unavailable in
the virtual environment. The deselected legacy migration test depends on an
owner's memory directories. All 573 checked source/test/config files stayed
unchanged during the run. No model benchmark or GPU was used.

The first resumed full run recorded 25 failures. Seven came from the qualification
runner's temporary-directory placement; 18 came from stale asset, packaging and
effort fixtures. They were corrected before the final complete passing run.
Independent gates also found and repaired package privacy leaks, archive path
collisions and malformed qualification-report acceptance. Earlier failures remain
in the dated handoff; passing reruns do not erase them.

A late actual-App reconnect probe found that Studio Stop could dismiss a terminal
approval while leaving its turn running. The repair separates whole-turn Stop
ownership from terminal Ctrl-C's approval denial. Independent probes and 26 focused
regressions passed before the final full suite above.

The final wheel passed layout and source comparison: all 268 packaged source files
matched the checkout and fresh installation. All 98 hash-locked runtime dependencies
plus Dream matched the lock, dependency compatibility passed, and five installed
CLI checks passed. This is a local candidate qualification, not a published release.
The final Stop-repair wheel was reinstalled into that same clean dependency
environment and its installed bytes checked again.

Focused gates overlap the full suite and must not be summed. A separate system-
Python native fixture passed five tests and 13 actual software GTK/WebKit/VTE
checks, including H.264 playback/pause/resume, reconnect, WebKit recovery, Stop,
Prompt Optimizer and preserved drafts. Fresh reviewers checked memory failure
handling, Gemini consumers, delivery, context, package privacy and optimizer
recovery. These are scoped results; native callback checks do not establish actual
Engine recovery, subtitle support or compatibility with every model/installation.

Memory tools now save before optional indexing and synchronize directory entries,
with explicit partial-result handling. Remaining work includes representative live
model/task calibration, native Engine recovery qualification, simultaneous editing
with isolated writers and conflict recovery, and durable revision lineage for
arbitrary output files. No universal production-grade or benchmark ranking is claimed.

For contributions, preserve unrelated work, identify the concrete behavior being
changed, and report meaningful verification and remaining limitations. Keep runtime
state and personal records out of commits. Use the public project tracking files
when recording development of a public checkout.
