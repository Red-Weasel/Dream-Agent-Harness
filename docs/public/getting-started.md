# Installation and providers

Install Python 3.12+ and uv, clone the repository, then run:

```bash
uv sync --locked --extra dev
uv run --locked dream
```

The picker lets you choose an available provider and workspace. Authenticate the
selected provider with its own supported CLI/login or API-key mechanism. Dream does
not bundle accounts, credentials, subscriptions, inference servers, or model weights.

| Provider | CLI selection | Prerequisite |
|---|---|---|
| Claude Agent SDK | `--provider anthropic` | Supported Claude authentication and SDK setup |
| Codex | `--provider codex` | Installed, authenticated Codex CLI |
| Grok | `--provider grok` | Installed, authenticated Grok CLI |
| Gemini | `--provider gemini` | Installed, authenticated Gemini CLI and eligible account |
| OpenAI-compatible API | `--provider openai` | Endpoint/model configuration and credentials as needed |
| xAI HTTP | `--provider xai` | `XAI_API_KEY` and selected model access |
| MachX | `dream local` or graphical selection | Separately installed engine and compatible model files |

Use `dream --help` and the graphical provider controls for available options.
Feature support belongs to the actual adapter/model combination.

## Linux desktop

The native shell requires GTK 3, VTE 2.91, and WebKitGTK 4.1. On Ubuntu/Debian:

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91 gir1.2-webkit2-4.1
uv run --locked dream desktop --check
uv run --locked dream desktop
```

The native process uses a system Python with GI bindings while the harness keeps
its Python environment. Run from a graphical session. Optional applications-menu
installation: `python3 scripts/install-desktop.py`.

## Optional tools

The hidden Studio inspection browser uses Playwright Chromium:

```bash
uv run --locked playwright install chromium
```

Other web browsing uses Camoufox; install its browser assets through its supported
setup. Search can use a separately configured SearXNG instance. Set
`DREAM_SEARXNG_DIR` for its checkout and `DREAM_SEARXNG_HOST` / `DREAM_SEARXNG_PORT`
for the endpoint; `DREAM_SEARXNG_AUTOSTART=0` disables automatic startup.

MachX paths are configurable with `DREAM_MACHX_DIR`, `DREAM_MODELS_DIR`, and
`DREAM_MODELS_DIRS`. Dream looks for the engine at `~/machx-inference-engine` (a clone of
[machx-inference-engine](https://github.com/Red-Weasel/machx-inference-engine)); otherwise point
`DREAM_MACHX_DIR` to the engine checkout containing `build/src/ie` and `scripts/env.sh`. Configure these for your installation; do not
expect another user's paths or model files to exist. Inspect available GPU/host
memory before starting substantial workloads.

Blender and FFmpeg are separate dependencies for relevant media workflows. Probe
actual render engines and device support before a costly render. A software-rendered
preview is a fallback, not evidence that hardware rendering is unavailable.

Return to the [README](../../README.md) or [runtime guide](runtime.md).
