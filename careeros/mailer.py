from __future__ import annotations
import os
import re
import smtplib
import ssl
from email.mime.text import MIMEText

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def send_email(to_address: str, subject: str, body: str) -> None:
    if not _EMAIL_RE.match(to_address):
        raise ValueError("Invalid recipient email address: " + repr(to_address))

    host = os.environ["CAREEROS_SMTP_HOST"]
    port = int(os.environ["CAREEROS_SMTP_PORT"])
    user = os.environ["CAREEROS_SMTP_USER"]
    password = os.environ["CAREEROS_SMTP_PASSWORD"]

    message = MIMEText(body)
    message["Subject"] = subject
    message["From"] = user
    message["To"] = to_address

    with smtplib.SMTP(host, port) as server:
        server.starttls(context=ssl.create_default_context())
        server.login(user, password)
        server.sendmail(user, [to_address], message.as_string())
