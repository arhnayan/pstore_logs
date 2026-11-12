"""SMTP email for host-down alerts. Config lives in the local settings table."""

from __future__ import annotations

import asyncio
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

_KEYS = ("host", "port", "security", "user", "password", "sender", "to")


@dataclass
class EmailConfig:
    host: str = ""
    port: int = 25
    security: str = "none"  # none | starttls | ssl
    user: str = ""
    password: str = ""
    sender: str = ""
    to: str = ""  # comma separated

    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender and self.recipients)

    @property
    def recipients(self) -> list[str]:
        return [a.strip() for a in self.to.split(",") if a.strip()]


async def get_email_config() -> EmailConfig:
    from app.deps import db

    values = {k: await db.get_setting(f"email_{k}") for k in _KEYS}
    cfg = EmailConfig()
    cfg.host = values["host"] or ""
    cfg.port = int(values["port"]) if (values["port"] or "").isdigit() else 25
    cfg.security = values["security"] or "none"
    cfg.user = values["user"] or ""
    cfg.password = values["password"] or ""
    cfg.sender = values["sender"] or ""
    cfg.to = values["to"] or ""
    return cfg


async def save_email_config(cfg: EmailConfig, *, keep_password: bool) -> None:
    from app.deps import db

    data = {
        "host": cfg.host, "port": str(cfg.port), "security": cfg.security,
        "user": cfg.user, "sender": cfg.sender, "to": cfg.to,
    }
    if not keep_password:
        data["password"] = cfg.password
    for key, value in data.items():
        await db.set_setting(f"email_{key}", value)


def _send_blocking(cfg: EmailConfig, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.sender
    msg["To"] = ", ".join(cfg.recipients)
    msg.set_content(body)
    smtp_cls = smtplib.SMTP_SSL if cfg.security == "ssl" else smtplib.SMTP
    with smtp_cls(cfg.host, cfg.port, timeout=30) as smtp:
        if cfg.security == "starttls":
            smtp.starttls()
        if cfg.user:
            smtp.login(cfg.user, cfg.password)
        smtp.send_message(msg, to_addrs=cfg.recipients)


async def send_email(subject: str, body: str, cfg: EmailConfig | None = None) -> None:
    """Raises on failure so callers can retry; raises RuntimeError if not configured."""
    cfg = cfg or await get_email_config()
    if not cfg.configured:
        raise RuntimeError("Email is not configured (Settings > Email alerts)")
    await asyncio.to_thread(_send_blocking, cfg, subject, body)
