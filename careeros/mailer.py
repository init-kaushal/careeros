from __future__ import annotations
import os
import smtplib
from email.mime.text import MIMEText


def send_email(to_address: str, subject: str, body: str) -> None:
    host = os.environ["CAREEROS_SMTP_HOST"]
    port = int(os.environ["CAREEROS_SMTP_PORT"])
    user = os.environ["CAREEROS_SMTP_USER"]
    password = os.environ["CAREEROS_SMTP_PASSWORD"]

    message = MIMEText(body)
    message["Subject"] = subject
    message["From"] = user
    message["To"] = to_address

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(user, password)
        server.sendmail(user, [to_address], message.as_string())
