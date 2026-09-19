import smtplib
from unittest.mock import MagicMock, patch

import pytest


def test_send_email_success(monkeypatch):
    from careeros.mailer import send_email
    monkeypatch.setenv("CAREEROS_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("CAREEROS_SMTP_PORT", "587")
    monkeypatch.setenv("CAREEROS_SMTP_USER", "me@example.com")
    monkeypatch.setenv("CAREEROS_SMTP_PASSWORD", "secret")

    mock_server = MagicMock()
    mock_smtp_cls = MagicMock()
    mock_smtp_cls.return_value.__enter__.return_value = mock_server
    mock_smtp_cls.return_value.__exit__.return_value = False

    with patch("careeros.mailer.smtplib.SMTP", mock_smtp_cls):
        send_email("jane@acme.com", "Hello", "Body text")

    mock_smtp_cls.assert_called_once_with("smtp.example.com", 587)
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with("me@example.com", "secret")
    mock_server.sendmail.assert_called_once()
    call_args = mock_server.sendmail.call_args[0]
    assert call_args[0] == "me@example.com"
    assert call_args[1] == ["jane@acme.com"]
    assert "Body text" in call_args[2]


def test_send_email_missing_env_var_raises_key_error(monkeypatch):
    from careeros.mailer import send_email
    monkeypatch.delenv("CAREEROS_SMTP_HOST", raising=False)
    monkeypatch.delenv("CAREEROS_SMTP_PORT", raising=False)
    monkeypatch.delenv("CAREEROS_SMTP_USER", raising=False)
    monkeypatch.delenv("CAREEROS_SMTP_PASSWORD", raising=False)
    with pytest.raises(KeyError):
        send_email("jane@acme.com", "Hello", "Body text")


def test_send_email_smtp_failure_propagates(monkeypatch):
    from careeros.mailer import send_email
    monkeypatch.setenv("CAREEROS_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("CAREEROS_SMTP_PORT", "587")
    monkeypatch.setenv("CAREEROS_SMTP_USER", "me@example.com")
    monkeypatch.setenv("CAREEROS_SMTP_PASSWORD", "wrong")

    mock_smtp_cls = MagicMock()
    mock_smtp_cls.return_value.__enter__.side_effect = smtplib.SMTPAuthenticationError(535, b"bad creds")

    with patch("careeros.mailer.smtplib.SMTP", mock_smtp_cls):
        with pytest.raises(smtplib.SMTPAuthenticationError):
            send_email("jane@acme.com", "Hello", "Body text")
