"""Example Sleepwalk notification connector: a Discord webhook to your own channel.
Not loaded by Dream: copy it into a plugin's sleepwalk/ folder and trust it (see README.md here)."""
import httpx

CONNECTOR = {"id": "discord", "label": "Discord", "kinds": ["notify"], "fields": [
    {"name": "webhook_url", "label": "Webhook URL", "secret": True}]}


def send(cfg, title, body):
    response = httpx.post(cfg["webhook_url"], timeout=30, json={"content": f"**Sleepwalk: {title}**\n{body}"[:2000],
                                                                "allowed_mentions": {"parse": []}})
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Discord answered HTTP {response.status_code}")
