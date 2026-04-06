"""
Tanishi Email Tools — Read and send emails.

Uses IMAP/SMTP with Gmail App Passwords (no Google Cloud project needed).

Setup:
1. Go to https://myaccount.google.com/apppasswords
2. Generate an app password for "Mail"
3. Add to .env: GMAIL_ADDRESS=you@gmail.com and GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
"""

import os
import email
import imaplib
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from datetime import datetime, timedelta

from tanishi.tools.registry import ToolDefinition


def _get_creds():
    addr = os.getenv("GMAIL_ADDRESS", "")
    pwd = os.getenv("GMAIL_APP_PASSWORD", "")
    return addr, pwd


async def read_emails(count: int = 5, folder: str = "INBOX", unread_only: bool = True) -> str:
    """Read recent emails from Gmail."""
    addr, pwd = _get_creds()
    if not addr or not pwd:
        return "Gmail not configured. Add GMAIL_ADDRESS and GMAIL_APP_PASSWORD to your .env file. Get an app password at https://myaccount.google.com/apppasswords"

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(addr, pwd)
        mail.select(folder)

        criteria = "UNSEEN" if unread_only else "ALL"
        status, messages = mail.search(None, criteria)

        if status != "OK":
            return "Failed to search emails."

        msg_ids = messages[0].split()
        if not msg_ids:
            return "No " + ("unread " if unread_only else "") + "emails found."

        # Get the latest N emails
        latest_ids = msg_ids[-count:]
        results = []

        for msg_id in reversed(latest_ids):
            status, msg_data = mail.fetch(msg_id, "(RFC822)")
            if status != "OK":
                continue

            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            # Decode subject
            subject = ""
            if msg["Subject"]:
                decoded = decode_header(msg["Subject"])
                subject = decoded[0][0]
                if isinstance(subject, bytes):
                    subject = subject.decode(decoded[0][1] or "utf-8", errors="replace")

            # Decode sender
            sender = msg.get("From", "Unknown")

            # Get date
            date = msg.get("Date", "Unknown date")

            # Get body (text part only)
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        payload = part.get_payload(decode=True)
                        if payload:
                            body = payload.decode("utf-8", errors="replace")[:500]
                        break
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="replace")[:500]

            results.append(
                f"From: {sender}\n"
                f"Subject: {subject}\n"
                f"Date: {date}\n"
                f"Preview: {body[:200]}...\n"
            )

        mail.logout()

        if not results:
            return "No emails to show."

        header = f"{'Unread' if unread_only else 'Recent'} emails ({len(results)}):\n\n"
        return header + "\n---\n".join(results)

    except imaplib.IMAP4.error as e:
        return f"Gmail login failed: {str(e)}. Check your app password."
    except Exception as e:
        return f"Email error: {str(e)}"


async def send_email(to: str, subject: str, body: str) -> str:
    """Send an email via Gmail SMTP."""
    addr, pwd = _get_creds()
    if not addr or not pwd:
        return "Gmail not configured. Add GMAIL_ADDRESS and GMAIL_APP_PASSWORD to .env"

    try:
        msg = MIMEMultipart()
        msg["From"] = addr
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(addr, pwd)
            server.send_message(msg)

        return f"Email sent to {to} with subject '{subject}'"

    except Exception as e:
        return f"Failed to send email: {str(e)}"


async def search_emails(query: str, count: int = 5) -> str:
    """Search emails by keyword in subject or body."""
    addr, pwd = _get_creds()
    if not addr or not pwd:
        return "Gmail not configured."

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(addr, pwd)
        mail.select("INBOX")

        # IMAP search
        status, messages = mail.search(None, f'(OR SUBJECT "{query}" BODY "{query}")')
        msg_ids = messages[0].split()

        if not msg_ids:
            return f"No emails matching '{query}'"

        latest = msg_ids[-count:]
        results = []

        for msg_id in reversed(latest):
            status, msg_data = mail.fetch(msg_id, "(RFC822)")
            if status != "OK":
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            subject = ""
            if msg["Subject"]:
                decoded = decode_header(msg["Subject"])
                subject = decoded[0][0]
                if isinstance(subject, bytes):
                    subject = subject.decode(decoded[0][1] or "utf-8", errors="replace")
            sender = msg.get("From", "Unknown")
            results.append(f"  From: {sender} | Subject: {subject}")

        mail.logout()
        return f"Found {len(results)} emails matching '{query}':\n" + "\n".join(results)

    except Exception as e:
        return f"Search error: {str(e)}"


def get_email_tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="read_emails",
            description="Read recent emails from Gmail inbox. Shows sender, subject, date, and preview. Use when user asks about their email, inbox, or messages.",
            input_schema={
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "description": "Number of emails to read.", "default": 5},
                    "unread_only": {"type": "boolean", "description": "Only show unread emails.", "default": True},
                },
                "required": [],
            },
            handler=read_emails,
            category="communication",
            risk_level="low",
        ),
        ToolDefinition(
            name="send_email",
            description="Send an email via Gmail. Use when user explicitly asks to send/compose an email.",
            input_schema={
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address."},
                    "subject": {"type": "string", "description": "Email subject line."},
                    "body": {"type": "string", "description": "Email body text."},
                },
                "required": ["to", "subject", "body"],
            },
            handler=send_email,
            category="communication",
            risk_level="high",
            requires_approval=True,
        ),
        ToolDefinition(
            name="search_emails",
            description="Search emails by keyword in subject or body.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keyword."},
                    "count": {"type": "integer", "description": "Max results.", "default": 5},
                },
                "required": ["query"],
            },
            handler=search_emails,
            category="communication",
            risk_level="low",
        ),
    ]
