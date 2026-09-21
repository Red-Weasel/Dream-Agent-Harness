# Contributing

Thanks for looking. Dream is a multi-model agent harness: a terminal client, a desktop
companion, a shared tool set, persistent memory, and a sandbox for the commands a model runs.

## Get it running

Dream needs **Python 3.12+** and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Red-Weasel/Dream-Agent-Harness.git
cd Dream-Agent-Harness
uv sync --locked --extra dev
uv run --locked dream            # terminal client
uv run --locked dream desktop    # desktop companion
```

Point it at whichever model you have. An API provider:

```bash
uv run --locked dream --provider anthropic --workspace /path/to/project
```

Or a local model through [MachX](https://github.com/Red-Weasel/machx-inference-engine):

```bash
uv run --locked dream local
```

## Run the tests

```bash
uv run --locked pytest tests/ -q
```

The suite is large (5,000+ tests) and takes about nine minutes. While iterating, run the files
your change touches:

```bash
uv run --locked pytest tests/test_snip.py -q
```

Some tests drive a real browser through Playwright. If those fail on a fresh clone:

```bash
uv run --locked playwright install chromium
```

A handful of tests need a local model server or GPU and will fail without one. Check whether a
failure also happens on `main` before assuming your change caused it.

## What a good change looks like

- **A test that fails before and passes after.** For a bug, write the reproduction first.
- **Surgical.** Touch what the change needs. Don't reformat or refactor around it.
- **Match the surrounding style,** including comment density. Comments here explain *why* a
  thing is the way it is, usually with the evidence that forced it — a measurement, a live
  failure, a date. If your change encodes a non-obvious decision, say what made it necessary.
- **Don't weaken a test to make something pass.** Several tests guard real invariants that are
  not obvious from the assertion alone. Two examples worth knowing about:
  - artifact preview frames are built from `srcdoc`, never a fetched URL — a test enforces it,
    and it is a security boundary;
  - a truncated answer is never silently retried, because a retry can repeat work that already
    had effects.
  If a test blocks you, read why it exists before changing it.
- **State uncertainty.** If you could not verify something, say so in the PR rather than
  implying it was checked.

## Things that will be looked at closely

- Anything touching `dream/core/execution.py` (the sandbox) or the permission flow. The sandbox
  boundary is that a model's commands cannot reach files outside the workspace.
- Anything that changes what is sent to a model provider, especially the prompt prefix. Local
  backends reuse a prompt cache; a change to the start of the prompt makes the server re-read
  the whole conversation, which costs minutes per turn on a large model.
- New dependencies. There should be a clear reason one is better than the standard library.

## Reporting a bug

Say what you ran, what you expected, and what happened, with the model and provider you used.
Logs live in `var/logs/` — please skim them for paths or keys before pasting.

## Security

Don't open a public issue for a vulnerability. Use GitHub's private vulnerability reporting on
this repository instead.
