"""Example Sleepwalk notification connector: ntfy (free on ntfy.sh, or your own server). The topic name works like a
password, so it is kept as a secret. Not loaded by Dream: copy it into a plugin's sleepwalk/ folder and trust it."""
import httpx

CONNECTOR = {"id": "ntfy", "label": "ntfy", "kinds": ["notify"], "fields": [
    {"name": "server", "label": "Server", "default": "https://ntfy.sh"},
    {"name": "topic", "label": "Your private topic", "secret": True}]}


def send(cfg, title, body):
    response = httpx.post(f"{cfg['server'].rstrip('/')}/{cfg['topic']}", timeout=30, content=body[:4000].encode(),
                          headers={"Title": f"Sleepwalk: {title}"[:200].encode("ascii", "replace").decode()})
    if response.status_code != 200:
        raise RuntimeError(f"ntfy answered HTTP {response.status_code}")
