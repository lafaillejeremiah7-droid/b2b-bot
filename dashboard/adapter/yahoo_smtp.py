from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from typing import Callable
from uuid import UUID

from django.conf import settings


class YahooSMTPError(RuntimeError):
    """Raised when Yahoo SMTP delivery cannot be completed."""


@dataclass(frozen=True)
class YahooSMTPReceipt:
    message_id: str


@dataclass(frozen=True)
class YahooSMTPConfig:
    host: str
    port: int
    username: str
    app_password: str
    use_ssl: bool = True
    timeout_seconds: float = 30.0

    @property
    def configured(self) -> bool:
        return bool(
            self.host.strip()
            and self.port > 0
            and self.username.strip()
            and self.app_password.strip()
        )


SMTPFactory = Callable[..., object]


def _stable_message_id(idempotency_key: UUID | str, sender: str) -> str:
    key = str(idempotency_key).strip()
    if not key:
        raise ValueError("Yahoo SMTP delivery requires an idempotency identity.")
    domain = sender.rsplit("@", 1)[-1].strip().lower() if "@" in sender else "b2b-bot.local"
    return f"<b2b-{key}@{domain}>"


class YahooSMTPDeliveryClient:
    """Yahoo/Turbify-compatible SMTP sender for the business mailbox.

    The password is expected to be an app password or mailbox-specific SMTP
    credential supplied by the deployment environment. A stable Message-ID is
    generated from the application idempotency key so retries carry the same
    message identity even though SMTP itself has no provider-side idempotency API.
    """

    def __init__(
        self,
        config: YahooSMTPConfig | None = None,
        *,
        sender_name: str = "",
        smtp_ssl_factory: SMTPFactory | None = None,
        smtp_factory: SMTPFactory | None = None,
    ) -> None:
        self.config = config or YahooSMTPConfig(
            host=settings.YAHOO_SMTP_HOST,
            port=settings.YAHOO_SMTP_PORT,
            username=settings.YAHOO_SMTP_USERNAME,
            app_password=settings.YAHOO_SMTP_APP_PASSWORD,
            use_ssl=settings.YAHOO_SMTP_USE_SSL,
            timeout_seconds=settings.YAHOO_SMTP_TIMEOUT_SECONDS,
        )
        self.sender_name = (sender_name or settings.OUTREACH_SENDER_NAME or "").strip()
        self._smtp_ssl_factory = smtp_ssl_factory or smtplib.SMTP_SSL
        self._smtp_factory = smtp_factory or smtplib.SMTP

    def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        idempotency_key: UUID | str,
    ) -> YahooSMTPReceipt:
        if not self.config.configured:
            raise YahooSMTPError(
                "Yahoo SMTP is not configured. Set the Yahoo SMTP username and app password in the private deployment environment."
            )
        recipient = (to or "").strip()
        if not recipient or "@" not in recipient:
            raise ValueError("A valid recipient email is required.")
        subject = (subject or "").strip()
        body = (body or "").strip()
        if not subject:
            raise ValueError("Email subject cannot be blank.")
        if not body:
            raise ValueError("Email body cannot be blank.")

        sender = self.config.username.strip()
        message_id = _stable_message_id(idempotency_key, sender)
        message = EmailMessage()
        message["To"] = recipient
        message["From"] = formataddr((self.sender_name, sender)) if self.sender_name else sender
        message["Subject"] = subject
        message["Message-ID"] = message_id
        message.set_content(body)

        try:
            if self.config.use_ssl:
                context = ssl.create_default_context()
                with self._smtp_ssl_factory(
                    self.config.host,
                    self.config.port,
                    timeout=self.config.timeout_seconds,
                    context=context,
                ) as smtp:
                    smtp.login(sender, self.config.app_password)
                    refused = smtp.send_message(message)
            else:
                context = ssl.create_default_context()
                with self._smtp_factory(
                    self.config.host,
                    self.config.port,
                    timeout=self.config.timeout_seconds,
                ) as smtp:
                    smtp.ehlo()
                    smtp.starttls(context=context)
                    smtp.ehlo()
                    smtp.login(sender, self.config.app_password)
                    refused = smtp.send_message(message)
        except (smtplib.SMTPException, OSError, TimeoutError) as exc:
            raise YahooSMTPError(f"Yahoo SMTP delivery failed: {type(exc).__name__}") from exc

        if refused:
            raise YahooSMTPError("Yahoo SMTP refused one or more recipients.")
        return YahooSMTPReceipt(message_id=message_id)


def yahoo_smtp_configured() -> bool:
    return YahooSMTPConfig(
        host=settings.YAHOO_SMTP_HOST,
        port=settings.YAHOO_SMTP_PORT,
        username=settings.YAHOO_SMTP_USERNAME,
        app_password=settings.YAHOO_SMTP_APP_PASSWORD,
        use_ssl=settings.YAHOO_SMTP_USE_SSL,
        timeout_seconds=settings.YAHOO_SMTP_TIMEOUT_SECONDS,
    ).configured


def get_yahoo_smtp_client() -> YahooSMTPDeliveryClient:
    return YahooSMTPDeliveryClient()
