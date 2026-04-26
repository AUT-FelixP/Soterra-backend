from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from .config import Settings

logger = logging.getLogger("soterra_backend.email")


class EmailService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.smtp_host)

    def send_registration_email(self, *, to_email: str, name: str, tenant_name: str) -> bool:
        subject = "Welcome to Soterra"
        body = (
            f"Hi {name},\n\n"
            f"Your Soterra account for {tenant_name} is ready.\n\n"
            f"Sign in here: {self.settings.app_base_url}/auth/sign-in\n\n"
            "Thanks,\nSoterra"
        )
        return self._send(to_email=to_email, subject=subject, body=body)

    def send_invitation_email(self, *, to_email: str, name: str, tenant_name: str) -> bool:
        subject = f"You have been invited to {tenant_name} on Soterra"
        body = (
            f"Hi {name},\n\n"
            f"You have been added to {tenant_name} on Soterra. Use the temporary password shared by your admin, "
            "then sign in and update it after access is confirmed.\n\n"
            f"Sign in here: {self.settings.app_base_url}/auth/sign-in\n\n"
            "Thanks,\nSoterra"
        )
        return self._send(to_email=to_email, subject=subject, body=body)

    def send_password_reset_email(self, *, to_email: str, name: str, token: str) -> bool:
        reset_url = f"{self.settings.app_base_url}/auth/reset-password?token={token}"
        subject = "Reset your Soterra password"
        body = (
            f"Hi {name},\n\n"
            "We received a request to reset your Soterra password.\n\n"
            f"Reset it here: {reset_url}\n\n"
            "This link expires in 1 hour. If you did not request this, you can ignore this email.\n\n"
            "Thanks,\nSoterra"
        )
        return self._send(to_email=to_email, subject=subject, body=body)

    def _send(self, *, to_email: str, subject: str, body: str) -> bool:
        if not self.is_configured:
            logger.warning("email_not_configured to=%s subject=%s", to_email, subject)
            return False

        message = EmailMessage()
        message["From"] = f"{self.settings.smtp_from_name} <{self.settings.smtp_from_email}>"
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(body)

        try:
            with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=15) as smtp:
                if self.settings.smtp_use_tls:
                    smtp.starttls()
                if self.settings.smtp_username:
                    smtp.login(self.settings.smtp_username, self.settings.smtp_password or "")
                smtp.send_message(message)
            logger.info("email_sent to=%s subject=%s", to_email, subject)
            return True
        except Exception:
            logger.exception("email_send_failed to=%s subject=%s", to_email, subject)
            return False
