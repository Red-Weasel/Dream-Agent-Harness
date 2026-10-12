"""PTY child for test_turn_prompt_box_pty.py: the REAL Dream TUI on the real local-model (OpenAI-compatible) backend,
pointed at the parent's controlled model server. Not a test file: the parent launches it inside a pseudo-terminal.

Env contract (set by the parent BEFORE this runs):
  DREAM_ROOT            -> a temp dir, so data/ var/ memory/ never touch the repo
  DREAM_FAKE_MODEL_URL  -> the controlled server's /v1 base URL
"""

import asyncio
import os
import sys
from dataclasses import replace

assert os.environ.get("DREAM_ROOT"), "parent must set DREAM_ROOT to a temp dir"

from dream.core import providers  # noqa: E402

providers.PROVIDERS["machx"] = replace(providers.PROVIDERS["machx"], base_url=os.environ["DREAM_FAKE_MODEL_URL"])

from dream.local import machx  # noqa: E402

machx.served_model_id = lambda: "controlled-model"  # never ask a real local engine which model it serves

from dream.tui import app as appmod  # noqa: E402


async def main():
    app = appmod.App(provider="machx", model="controlled-model", consolidate_on_exit=False,
                     workspace=os.environ["DREAM_ROOT"], gui=False)
    await app.run()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
