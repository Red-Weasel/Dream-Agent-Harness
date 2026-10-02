"""Gmail (DREAM-160): read the last day's unread message headers over IMAP (read-only: EXAMINE and BODY.PEEK, so
nothing is marked read), and send a run's notification by SMTP to the same address only. A Google app password."""
from __future__ import annotations

import email.header
import email.utils
import imaplib
import smtplib
from datetime import date, timedelta
from email.message import EmailMessage

CONNECTOR = {"id": "gmail", "label": "Gmail", "kinds": ["context", "notify"], "fields": [
    {"name": "address", "label": "Gmail address", "pattern": r"[^@\s,;<>\"']+@[^@\s,;<>\"']+"},
    {"name": "app_password", "label": "App password", "secret": True},
    {"name": "imap_host", "label": "IMAP server", "default": "imap.gmail.com"},
    {"name": "smtp_host", "label": "SMTP server", "default": "smtp.gmail.com"}]}


def _text(value) -> str:
    return str(email.header.make_header(email.header.decode_header(value or "")))


def fetch(cfg: dict) -> str:
    with imaplib.IMAP4_SSL(cfg["imap_host"], 993, timeout=30) as imap:
        imap.login(cfg["address"], cfg["app_password"])
        imap.select("INBOX", readonly=True)
        since = (date.today() - timedelta(days=1)).strftime("%d-%b-%Y")
        _, found = imap.search(None, "UNSEEN", "SINCE", since)
        lines = []
        for number in found[0].split()[-30:][::-1]:
            _, data = imap.fetch(number, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            message = email.message_from_bytes(data[0][1])
            lines.append(f"- {_text(message['From'])}: {_text(message['Subject'])} ({message['Date']})")
    return "\n".join(lines) or "No unread messages in the last day."


def send(cfg: dict, title: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = message["To"] = cfg["address"]          # to the owner's own address, never anyone else
    message["Subject"] = "Sleepwalk: " + " ".join(title.split())       # never a header break
    message["Date"] = email.utils.formatdate(localtime=True)
    message.set_content(body)
    with smtplib.SMTP_SSL(cfg["smtp_host"], 465, timeout=30) as smtp:
        smtp.login(cfg["address"], cfg["app_password"])
        smtp.send_message(message, to_addrs=[cfg["address"]])
