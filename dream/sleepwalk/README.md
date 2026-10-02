# Sleepwalk automations

Sleepwalk (left rail, moon icon) runs your written instructions on a schedule while Dream is open, with a
read-only runner, and keeps every result in **Runs**. Runs only read and notify: they never send replies,
create events or write files. Runners are isolated from your own CLI tools and settings: Codex runs without your
plugins, apps, hooks, MCP servers and project instructions, Claude without your settings, and the Grok and Gemini
CLIs, which cannot be isolated, are refused.

## Background runs

**Background runs · Off** (top of the page) turns on a systemd user timer that runs due automations every
15 minutes while you are logged in, also with Dream closed. It uses cloud runners only: an automation on the
local model uses its cloud fallback, or is recorded as skipped. With a Dream window open, Dream runs the
schedule itself and the timer does nothing. Turning it off removes the timer.

## Connectors

Open an automation, then **Connectors** under the instructions. Tick a connector to give its data to the
run as read-only context; **Set up** stores its settings. Secrets (passwords, tokens, the private
calendar address) go to the system keyring and never to a file; install the optional extra first
(`uv pip install keyring`, or the `sleepwalk` extra; try `--dry-run` first in an existing environment).
Without the keyring, set the environment variable the form's error names, for example
`DREAM_SLEEPWALK_GMAIL_APP_PASSWORD`.

- **Gmail** (context + email notification): your address and a Google *app password* (needs 2-Step
  Verification; Google Account > Security > App passwords). Reads the last day's unread message
  headers without marking them read; notifications go only to that same address.
- **Google Calendar** (context): the calendar's *Secret address in iCal format* (Google Calendar >
  Settings > your calendar > Integrate calendar). Today's and tomorrow's events, repeats included.
- **Telegram** (notification): create a bot with BotFather, send it one message, and enter its token
  and your chat id. Messages go only to that chat.

A plugin can add a connector as one file in `plugins/<name>/sleepwalk/`. Dream imports it only after
you review and trust its exact source, like a plugin tool: `dream extensions review tool:plugin/<name>/sleepwalk/<file name without .py>`, then
`dream extensions trust tool:plugin/<name>/sleepwalk/<file> --sha256 <reviewed digest>` (or Studio's **Review Python
source**). Ready-made examples for Pushover, ntfy, Slack and Discord are in [examples](examples/README.md).

## Notifications and attachments

**Notification** picks where a finished run is announced: Dream always (the run record and a notice
while Dream is open), plus any notification connector you set up. **Attachments** (after the first
Save; 10 files, 2 MB each) are copied into each run's private folder, and text files are also quoted
in the run's instructions.
