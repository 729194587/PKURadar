"""Plain-text SMTP delivery with sanitized transport errors."""
import os
import smtplib
import ssl
from email.message import EmailMessage
from zoneinfo import ZoneInfo


class EmailDeliveryError(RuntimeError):
    pass


class EmailWriter:
    def configure(self):
        names = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "RADAR_EMAIL_TO")
        values = {name: os.environ.get(name, "") for name in names}
        missing = [name for name, value in values.items() if not value.strip()]
        if missing:
            raise ValueError("Missing email configuration: " + ", ".join(missing))
        try:
            port = int(values["SMTP_PORT"])
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            raise ValueError("SMTP_PORT must be an integer between 1 and 65535") from None
        sender = os.environ.get("RADAR_EMAIL_FROM", "").strip() or values["SMTP_USER"]
        for name, value in (*values.items(), ("RADAR_EMAIL_FROM", sender)):
            if name != "SMTP_PASSWORD" and ("\r" in value or "\n" in value):
                raise ValueError(f"Invalid email configuration: {name} contains a newline")
        self._host, self._port = values["SMTP_HOST"].strip(), port
        self._user, self._password = values["SMTP_USER"].strip(), values["SMTP_PASSWORD"]
        self._recipient, self._sender = values["RADAR_EMAIL_TO"].strip(), sender

    def write(self, digest, *, now, recommendation_count):
        if not recommendation_count:
            return
        try:
            message = EmailMessage()
            date = now.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
            message["Subject"] = f"PKU Radar · {date} · {recommendation_count} 条值得关注"
            message["From"], message["To"] = self._sender, self._recipient
            message.set_content(digest, charset="utf-8")
            context = ssl.create_default_context()
            transport = smtplib.SMTP_SSL if self._port == 465 else smtplib.SMTP
            options = {"timeout": 30}
            if self._port == 465:
                options["context"] = context
            with transport(self._host, self._port, **options) as smtp:
                if self._port != 465:
                    smtp.starttls(context=context)
                smtp.login(self._user, self._password)
                if smtp.send_message(message):
                    raise smtplib.SMTPRecipientsRefused({})
        except Exception as exc:
            # Never copy server replies or credentials into errors or tracebacks.
            if isinstance(exc, smtplib.SMTPAuthenticationError):
                reason = "authentication rejected; check SMTP credentials/app password"
            elif isinstance(exc, smtplib.SMTPNotSupportedError):
                reason = "required SMTP TLS/authentication is unsupported"
            elif isinstance(exc, smtplib.SMTPRecipientsRefused):
                reason = "recipient refused; check RADAR_EMAIL_TO"
            elif isinstance(exc, (TimeoutError, OSError)):
                reason = "SMTP connection or transport failed"
            else:
                reason = "SMTP message delivery failed"
            raise EmailDeliveryError("Email delivery failed: " + reason) from None
