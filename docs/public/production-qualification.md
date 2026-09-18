# Production qualification

Dream's qualification runner checks adapter and recovery contracts with synthetic
transports. Run it from the development checkout with the development dependencies:

```sh
.venv/bin/python scripts/qualify_production.py --output /tmp/dream-qualification.json
.venv/bin/python scripts/qualify_production.py --gate http --timeout 180
```

The fixed groups cover HTTP capabilities and effort, CLI/SDK completion and
cancellation, computer-action outcomes, permissions and tool provenance. The
watchdog bounds each fixture group. It does not add a model runtime budget.

`delivery` checks failed completion attempts, verifier execution evidence and
incomplete media. `continuity` checks bounded context, reported tool outcomes and
memory directory durability failures.
`package` checks synthetic archive rejection, curated selection and diagnostic
assets; it does not build or install a wheel. Native UI and installation checks
below remain separate.

The JSON report records Python version, timestamp, selected source/test hashes,
test counts, failed/error/skipped test IDs and exit status. `verified` requires
successful execution with no required skips. A skipped group is `not_run`;
missing pytest or an interpreter that cannot start is `unavailable`. Timeout,
nonzero exit, missing test results and empty groups cannot pass. Listed files
must stay unchanged during the gate. This is a limited source fingerprint,
not a complete dependency audit. Failed groups can be rerun directly with pytest
for their detailed fixture output.

Reports validate pytest's JUnit structure and declared failure/error/skip counts.
`tests` counts visible testcase elements; `reported_tests` retains pytest's suite
total, which can also include passing unittest subtests without individual elements.
These counts must not be presented as two separate sets of executed tests.

Child configuration points to private HOME/XDG, Dream and Codex directories. Known
API credentials, ambient Dream options and Python import overrides are removed.
Optional memory processing is disabled and accelerator devices are hidden from
the child; the parent environment is unchanged.
The runner does not intentionally call live models or use the owner's desktop.
It is a test runner, not an OS sandbox for arbitrary pytest plugins or modified
test code. Review checkout dependencies and test changes as normal.

## Native desktop workflow

The opt-in native fixture runs real GTK/VTE/WebKit and a model-free Studio server.
With a separately started, unused Xvfb display (replace `:99` with its number),
system GI dependencies and the checkout virtual environment available:

```sh
DISPLAY=:99 /usr/bin/python3 scripts/native_workflow_qualification.py --output /tmp/dream-native-check
/usr/bin/python3 tests/test_native_workflow_qualification.py
```

The fixture isolates Dream/XDG settings and credentials before importing Dream,
forces software rendering, and checks H.264 playback, malformed media, reconnect,
WebKit recovery, Stop/subsequent callback dispatch, native Prompt Optimizer and
draft-preserving transfer. It requires an observed zero child exit status. This
covers UI/callbacks, not real Engine recovery, subtitles, owner acceptance or model
quality. Never point it at an owner's active display.

## Installation and privacy checks

Build a candidate wheel separately from runtime data:

```sh
uv build --wheel --offline --out-dir /tmp/dream-wheel
uv venv /tmp/dream-install
uv pip install --offline --python /tmp/dream-install/bin/python /tmp/dream-wheel/dream-0.1.0-py3-none-any.whl
```

An offline dependency install requires cached distributions. If dependencies are
reused from a development environment, record that explicitly; it does not prove
installation on a new machine. Run the installed interpreter outside the checkout
with temporary `DREAM_ROOT`, disabled external skill discovery and isolated
provider configuration. Check the imported Dream path, bundled skills, `--help`,
`status`, `runs` and `profile`. These checks should not load a model.

For a reproducible fresh dependency environment, export the frozen lockfile with
hashes and install its runtime requirements into a new temporary venv before
installing the candidate wheel with `--no-deps`. Record the lockfile hash and run
`uv pip check` against that environment. Downloading Python dependencies does not
qualify model inference, browser binaries or native desktop prerequisites.

Controls → the Dream version details shows the process ID, package import time,
startup source ID and whether selected core files have changed on disk. The ID is
a bounded content snapshot at package import, not a proof of every loaded module,
plugin or provider version. Missing/unreadable selected files produce Unknown.
An older server without this field shows Not reported. Restart when convenient to
load changed Python code; the diagnostic itself never restarts Dream.

Inspect wheel members and compare packaged code, assets and skills to the
candidate source. In a disposable source copy, place synthetic canaries in
`data`, `memory`, `var`, `artifacts`, `.remember`, `.dream`, `.agents`, `.codex`
and `.codebase-memory`, including nested copies inside `dream` and curated skill
folders. Add synthetic environment, database and model-weight files. Confirm none
ship while memory implementation, curated skills and UI assets remain. Skills use
ordinary selected paths and source remapping; directory force-includes bypass
exclusions. The layout audit rejects duplicate/noncanonical paths, private state,
symlinks, foreign metadata namespaces and file/directory collisions. It checks
required assets but is not an exhaustive content manifest. Never copy real private
sessions or credentials into a release fixture. Wheel exclusions do not remove
private files from Git history or audit arbitrary source content for secrets.

## What remains separate

Live endpoint authentication, actual image acceptance, provider-native behavior,
GPU availability, native daily use and model quality require their own evidence.
Passing fixtures cannot establish universal model compatibility or improved task
quality. Local model tests and benchmarks remain deferred for the current work.
