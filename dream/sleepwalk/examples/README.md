# Example Sleepwalk connectors

These four notification connectors are examples. Dream does not load anything from this folder. To use one:

1. Make a plugin folder, for example `plugins/notify-extra/`, with a `plugin.yaml` containing `name: notify-extra`.
2. Copy the connector file into `plugins/notify-extra/sleepwalk/` (for example `ntfy.py`).
3. Review and trust its exact source (Dream never imports a plugin connector before this):
   `dream extensions review tool:plugin/notify-extra/sleepwalk/ntfy`, then
   `dream extensions trust tool:plugin/notify-extra/sleepwalk/ntfy --sha256 <the reviewed digest>`.
4. Restart Dream, open an automation, **Connectors** > **Set up**, and tick it under **Notification**.

| File | Service | You need |
|---|---|---|
| `pushover.py` | Pushover push notifications (one-time app purchase per platform) | an application token and your user key |
| `ntfy.py` | ntfy (free on ntfy.sh, or your own server) | a private topic name (it works like a password) |
| `slack.py` | Slack | an incoming webhook URL for your own channel |
| `discord.py` | Discord | a webhook URL for your own channel |

Every secret you enter is kept in the system keyring (or a `DREAM_SLEEPWALK_<CONNECTOR>_<FIELD>` environment
variable), never in a file. A connector is one file: `CONNECTOR = {id, label, kinds, fields}` and
`send(cfg, title, body)` for notifications or `fetch(cfg) -> str` for read-only context. See `../connectors/` for
the built-in ones.
