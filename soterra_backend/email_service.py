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
        sign_in_url = f"{self.settings.app_base_url}/auth/sign-in"
        text = (
            f"Hi {name},\n\n"
            f"Your Soterra account for {tenant_name} is ready.\n\n"
            f"Sign in here: {sign_in_url}\n\n"
            "Thanks,\nThe Soterra team"
        )
        html = self._render_html(
            title="Welcome to Soterra",
            preview=f"Your account for {tenant_name} is ready.",
            greeting=f"Hi {name},",
            body=f"Your Soterra account for {tenant_name} is ready. You can now sign in and start managing inspection reports.",
            button_label="Sign in",
            button_url=sign_in_url,
        )
        return self._send(to_email=to_email, subject=subject, text=text, html=html)

    def send_invitation_email(self, *, to_email: str, name: str, tenant_name: str) -> bool:
        subject = f"You have been invited to {tenant_name} on Soterra"
        sign_in_url = f"{self.settings.app_base_url}/auth/sign-in"
        text = (
            f"Hi {name},\n\n"
            f"You have been added to {tenant_name} on Soterra. Use the temporary password shared by your admin, "
            "then sign in and update it after access is confirmed.\n\n"
            f"Sign in here: {sign_in_url}\n\n"
            "Thanks,\nThe Soterra team"
        )
        html = self._render_html(
            title=f"You have been invited to {tenant_name}",
            preview="Your Soterra access is ready.",
            greeting=f"Hi {name},",
            body=(
                f"You have been added to {tenant_name} on Soterra. Use the temporary password shared by your admin, "
                "then sign in and update it after access is confirmed."
            ),
            button_label="Sign in",
            button_url=sign_in_url,
        )
        return self._send(to_email=to_email, subject=subject, text=text, html=html)

    def send_password_reset_email(self, *, to_email: str, name: str, token: str) -> bool:
        reset_url = f"{self.settings.app_base_url}/auth/reset-password?token={token}"
        subject = "Reset your Soterra password"
        text = (
            f"Hi {name},\n\n"
            "We received a request to reset your Soterra password.\n\n"
            f"Reset it here: {reset_url}\n\n"
            "This link expires in 1 hour. If you did not request this, you can ignore this email.\n\n"
            "Thanks,\nThe Soterra team"
        )
        html = self._render_html(
            title="Reset your password",
            preview="Use this secure link to reset your Soterra password.",
            greeting=f"Hi {name},",
            body=(
                "We received a request to reset your Soterra password. This link expires in 1 hour. "
                "If you did not request this, you can ignore this email."
            ),
            button_label="Reset password",
            button_url=reset_url,
        )
        return self._send(to_email=to_email, subject=subject, text=text, html=html)

    def _send(self, *, to_email: str, subject: str, text: str, html: str) -> bool:
        if not self.is_configured:
            logger.warning("email_not_configured to=%s subject=%s", to_email, subject)
            return False

        message = EmailMessage()
        message["From"] = f"{self.settings.smtp_from_name} <{self.settings.smtp_from_email}>"
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(text)
        message.add_alternative(html, subtype="html")

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

    def _render_html(
        self,
        *,
        title: str,
        preview: str,
        greeting: str,
        body: str,
        button_label: str,
        button_url: str,
    ) -> str:
        escaped = {
            "title": _escape_html(title),
            "preview": _escape_html(preview),
            "greeting": _escape_html(greeting),
            "body": _escape_html(body),
            "button_label": _escape_html(button_label),
            "button_url": _escape_html(button_url),
        }
        return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escaped["title"]}</title>
  </head>
  <body style="margin:0;background:#f4f7f5;color:#17231f;font-family:Arial,Helvetica,sans-serif;">
    <span style="display:none;max-height:0;overflow:hidden;color:transparent;">{escaped["preview"]}</span>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f4f7f5;padding:32px 16px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#ffffff;border:1px solid #dce7df;border-radius:8px;overflow:hidden;">
            <tr>
              <td style="background:#12372f;padding:24px 28px;">
                <div style="font-size:24px;line-height:1.2;font-weight:700;color:#ffffff;letter-spacing:0;">Soterra</div>
                <div style="font-size:13px;line-height:1.5;color:#b7d9c5;margin-top:6px;">Inspection intelligence for safer buildings</div>
              </td>
            </tr>
            <tr>
              <td style="padding:32px 28px;">
                <h1 style="margin:0 0 18px;font-size:24px;line-height:1.25;color:#12372f;">{escaped["title"]}</h1>
                <p style="margin:0 0 16px;font-size:16px;line-height:1.6;">{escaped["greeting"]}</p>
                <p style="margin:0 0 24px;font-size:16px;line-height:1.6;color:#31443d;">{escaped["body"]}</p>
                <a href="{escaped["button_url"]}" style="display:inline-block;background:#1b7f5a;color:#ffffff;text-decoration:none;font-size:15px;font-weight:700;line-height:1;padding:14px 18px;border-radius:6px;">{escaped["button_label"]}</a>
              </td>
            </tr>
            <tr>
              <td style="border-top:1px solid #e4ece7;padding:18px 28px;color:#5f7069;font-size:12px;line-height:1.5;">
                This message was sent by Soterra. If this was unexpected, contact your account administrator.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""


def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )
