"""Example Sleepwalk notification connector: a Slack incoming webhook to your own channel.
Not loaded by Dream: copy it into a plugin's sleepwalk/ folder and trust it (see README.md here)."""
import httpx

CONNECTOR = {"id": "slack", "label": "Slack", "kinds": ["notify"], "fields": [
    {"name": "webhook_url", "label": "Incoming webhook URL", "secret": True}]}


def send(cfg, title, body):
    response = httpx.post(cfg["webhook_url"], timeout=30, json={"text": f"*Sleepwalk: {title}*\n{body}"[:3900]})
    if response.status_code != 200:
        raise RuntimeError(f"Slack answered HTTP {response.status_code}")
