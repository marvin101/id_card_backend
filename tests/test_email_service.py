import smtplib

import pytest

from email import message_from_bytes
from email.policy import default

from app.core import email
from app.core.email import (
    EmailDeliveryError,
    send_email,
)


class FakeSMTP:

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.messages = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def ehlo(self):
        pass

    def starttls(self, context=None):
        assert context is not None

    def login(self, username, password):
        self.username = username
        self.password = password

    def send_message(self, message):
        self.messages.append(message)
        return {}


def configure_smtp(monkeypatch):
    monkeypatch.setattr(
        email.settings,
        "smtp_host",
        "smtp-relay.brevo.com",
    )
    monkeypatch.setattr(
        email.settings,
        "smtp_port",
        587,
    )
    monkeypatch.setattr(
        email.settings,
        "smtp_username",
        "test-login",
    )
    monkeypatch.setattr(
        email.settings,
        "smtp_password",
        "test-password",
    )
    monkeypatch.setattr(
        email.settings,
        "smtp_from_email",
        "noreply@campusid.co.in",
    )
    monkeypatch.setattr(
        email.settings,
        "smtp_from_name",
        "CampusID",
    )
    monkeypatch.setattr(
        email.settings,
        "smtp_use_starttls",
        True,
    )


def test_send_email_requires_smtp_credentials(monkeypatch):
    monkeypatch.setattr(
        email.settings,
        "smtp_username",
        "",
    )
    monkeypatch.setattr(
        email.settings,
        "smtp_password",
        "",
    )

    with pytest.raises(EmailDeliveryError):
        send_email(
            recipient="user@example.com",
            subject="Test",
            text_body="Test message",
        )


def test_send_email_uses_starttls_and_authentication(monkeypatch):
    configure_smtp(monkeypatch)

    smtp_instances = []

    def smtp_factory(host, port, timeout):
        instance = FakeSMTP(host, port, timeout)
        smtp_instances.append(instance)
        return instance

    monkeypatch.setattr(
        email.smtplib,
        "SMTP",
        smtp_factory,
    )

    send_email(
        recipient="user@example.com",
        subject="CampusID Test",
        text_body="Plain text message",
    )

    smtp = smtp_instances[0]

    assert smtp.host == "smtp-relay.brevo.com"
    assert smtp.port == 587
    assert smtp.username == "test-login"
    assert smtp.password == "test-password"
    assert len(smtp.messages) == 1


def test_send_email_supports_html_and_plain_text(monkeypatch):
    configure_smtp(monkeypatch)

    smtp_instances = []

    def smtp_factory(host, port, timeout):
        instance = FakeSMTP(host, port, timeout)
        smtp_instances.append(instance)
        return instance

    monkeypatch.setattr(
        email.smtplib,
        "SMTP",
        smtp_factory,
    )

    send_email(
        recipient="user@example.com",
        subject="HTML Test",
        text_body="Plain text version",
        html_body="<h1>CampusID</h1>",
    )

    message = smtp_instances[0].messages[0]

    assert message.get_content_type() == "multipart/alternative"

    parts = list(message.iter_parts())

    assert len(parts) == 2
    assert parts[0].get_content() == "Plain text version\n"
    assert "<h1>CampusID</h1>" in parts[1].get_content()


def test_send_email_raises_safe_error_on_smtp_failure(monkeypatch):
    configure_smtp(monkeypatch)

    class FailingSMTP(FakeSMTP):

        def login(self, username, password):
            raise smtplib.SMTPAuthenticationError(
                535,
                b"Authentication failed",
            )

    monkeypatch.setattr(
        email.smtplib,
        "SMTP",
        FailingSMTP,
    )

    with pytest.raises(
        EmailDeliveryError,
        match="Email delivery failed",
    ):
        send_email(
            recipient="user@example.com",
            subject="Test",
            text_body="Test",
        )