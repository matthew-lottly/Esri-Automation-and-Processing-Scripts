"""E-mail delivery. EMAIL_MODE=smtp sends for real; EMAIL_MODE=file writes .eml files (testing/dry-run)."""
from __future__ import annotations

import logging
import smtplib
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from .config import Settings

log = logging.getLogger(__name__)


class EmailError(RuntimeError):
    pass


class EmailNotifier:
    def __init__(self, settings: Settings, retries: int = 3, backoff_s: float = 2.0):
        self.s, self.retries, self.backoff = settings, retries, backoff_s

    def build(self, subject: str, text: str, html: str | None, to: list[str], attachments: list[Path] | None = None) -> EmailMessage:
        if not to:
            raise EmailError("no recipients configured")
        m = EmailMessage()
        m["Subject"], m["From"], m["To"] = subject, self.s.email_from, ", ".join(to)
        m["Date"], m["Message-ID"] = formatdate(localtime=True), make_msgid()
        m.set_content(text)
        if html:
            m.add_alternative(html, subtype="html")
        for p in attachments or []:
            m.add_attachment(Path(p).read_bytes(), maintype="application", subtype="pdf", filename=Path(p).name)
        return m

    def send(self, subject: str, text: str, to: list[str], html: str | None = None, attachments: list[Path] | None = None) -> None:
        msg = self.build(subject, text, html, to, attachments)
        if self.s.email_mode == "file":
            out = Path(self.s.email_outbox_dir)
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{int(time.time() * 1000)}_{abs(hash(subject)) % 10**6}.eml").write_bytes(bytes(msg))
            log.info("EMAIL (file mode) '%s' -> %s", subject, to)
            return
        last: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                with smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=30) as smtp:
                    if self.s.smtp_starttls:
                        smtp.starttls()
                    if self.s.smtp_username:
                        smtp.login(self.s.smtp_username, self.s.smtp_password)
                    smtp.send_message(msg)
                return
            except (smtplib.SMTPException, OSError) as exc:
                last = exc
                log.warning("SMTP attempt %d/%d failed: %s", attempt, self.retries, exc)
                time.sleep(self.backoff * attempt)
        raise EmailError(f"could not send '{subject}': {last}")
