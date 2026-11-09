import logging
import smtplib
import ssl

from email.message import EmailMessage
from email.utils import formataddr

from app.core.config import settings


logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """Raised when an email cannot be delivered."""


def send_email(
    *,
    recipient: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> None:
    """
    Send a transactional email through the configured SMTP provider.

    Supports plain-text and HTML email alternatives.

    SMTP credentials are never included in application logs.
    """

    if not settings.smtp_username or not settings.smtp_password:
        raise EmailDeliveryError(
            "SMTP credentials are not configured."
        )

    if not settings.smtp_from_email:
        raise EmailDeliveryError(
            "SMTP sender address is not configured."
        )

    message = EmailMessage()

    message["Subject"] = subject
    message["From"] = formataddr(
        (
            settings.smtp_from_name,
            settings.smtp_from_email,
        )
    )
    message["To"] = recipient

    message.set_content(text_body)

    if html_body:
        message.add_alternative(
            html_body,
            subtype="html",
        )

    try:
        with smtplib.SMTP(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
        ) as server:

            server.ehlo()

            if settings.smtp_use_starttls:
                server.starttls(
                    context=ssl.create_default_context()
                )
                server.ehlo()

            server.login(
                settings.smtp_username,
                settings.smtp_password,
            )

            refused_recipients = server.send_message(message)

            if refused_recipients:
                raise EmailDeliveryError(
                    "SMTP server refused the recipient."
                )

    except EmailDeliveryError:
        raise

    except (OSError, smtplib.SMTPException) as exc:
        logger.error(
            "SMTP email delivery failed (exception_type=%s).",
            type(exc).__name__,
        )
        raise EmailDeliveryError(
        "Email delivery failed."
        ) from None