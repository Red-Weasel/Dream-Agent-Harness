"""Telegram (DREAM-160): a run's notification from the owner's own bot to the owner's own chat."""
from __future__ import annotations

import httpx

API = "https://api.telegram.org"
CONNECTOR = {"id": "telegram", "label": "Telegram", "kinds": ["notify"], "fields": [
    {"name": "bot_token", "label": "Bot token (from BotFather)", "secret": True},
    {"name": "chat_id", "label": "Your chat id", "pattern": r"-?[0-9]+|@[A-Za-z0-9_]{5,32}"}]}


def send(cfg: dict, title: str, body: str) -> None:
    text = f"Sleepwalk: {title}\n\n{body}"
    response = httpx.post(f"{API}/bot{cfg['bot_token']}/sendMessage", timeout=30,
                          json={"chat_id": cfg["chat_id"], "text": text[:4000], "disable_web_page_preview": True})
    if response.status_code != 200 or not response.json().get("ok"):
        raise RuntimeError(f"Telegram answered HTTP {response.status_code}")
