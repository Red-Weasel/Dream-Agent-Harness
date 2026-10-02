"""Example Sleepwalk notification connector: Pushover (push notifications; the app is a one-time purchase per platform).
Not loaded by Dream: copy it into a plugin's sleepwalk/ folder and trust it (see README.md here)."""
import httpx

API = "https://api.pushover.net/1/messages.json"
CONNECTOR = {"id": "pushover", "label": "Pushover", "kinds": ["notify"], "fields": [
    {"name": "app_token", "label": "Application API token", "secret": True},
    {"name": "user_key", "label": "Your user key", "secret": True}]}


def send(cfg, title, body):
    response = httpx.post(API, timeout=30, data={"token": cfg["app_token"], "user": cfg["user_key"],
                                                 "title": f"Sleepwalk: {title}"[:250], "message": body[:1024]})
    if response.status_code != 200:
        raise RuntimeError(f"Pushover answered HTTP {response.status_code}")
