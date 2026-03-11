"""
resend_tools.py — Email delivery via Resend API.

Used by email_architect and any agent that needs to send a real email.
All sends are logged to Supabase (email_log table) — non-fatal if logging fails.

Usage:
    from tools.resend_tools import ResendMailer
    mailer = ResendMailer()
    result = mailer.send(
        to="client@example.com",
        subject="Your proposal is ready",
        html="<p>Here it is...</p>",
        from_name="JonnyAI",   # optional, defaults to settings
    )

Resend docs: https://resend.com/docs/api-reference/emails/send-email
"""

import uuid
import structlog
from datetime import datetime, timezone
from typing import Optional

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import settings

log = structlog.get_logger()

RESEND_API_URL = "https://api.resend.com/emails"
MAX_RETRIES    = 3
RETRY_MIN_S    = 2
RETRY_MAX_S    = 30


class ResendError(Exception):
    """Raised when Resend returns a non-2xx response after all retries."""


class ResendMailer:
    """
    Thin wrapper around the Resend HTTP API.
    Retries on transient network errors. Logs every send to Supabase.
    """

    def __init__(self):
        self._api_key   = settings.resend_api_key
        self._from_addr = getattr(settings, "resend_from_email", "hello@jonnyai.co.uk")
        self._from_name = getattr(settings, "resend_from_name", "JonnyAI")
        self._supa_url  = settings.supabase_url
        self._supa_key  = settings.supabase_service_role_key

    # ── Public ────────────────────────────────────────────────────────────────

    def send(
        self,
        to:        str | list[str],
        subject:   str,
        html:      str,
        text:      Optional[str] = None,
        from_name: Optional[str] = None,
        reply_to:  Optional[str] = None,
        tags:      Optional[dict] = None,  # e.g. {"client": "marzer", "type": "proposal"}
    ) -> dict:
        """
        Send an email via Resend.

        Returns:
            {"id": resend_email_id, "status": "sent"} on success
            {"error": str, "status": "failed"} on permanent failure
        """
        recipients = [to] if isinstance(to, str) else to
        sender     = f"{from_name or self._from_name} <{self._from_addr}>"
        email_id   = str(uuid.uuid4())

        payload: dict = {
            "from":    sender,
            "to":      recipients,
            "subject": subject,
            "html":    html,
        }
        if text:
            payload["text"] = text
        if reply_to:
            payload["reply_to"] = reply_to

        log.info("resend.sending", email_id=email_id, to=recipients, subject=subject[:60])

        try:
            resend_id = self._call_resend(payload)
            log.info("resend.sent", email_id=email_id, resend_id=resend_id)
            self._log_to_supabase(email_id, recipients, subject, "sent", resend_id, tags)
            return {"id": resend_id, "status": "sent", "email_id": email_id}

        except ResendError as exc:
            msg = str(exc)
            log.error("resend.failed", email_id=email_id, error=msg)
            self._log_to_supabase(email_id, recipients, subject, "failed", None, tags, error=msg)
            return {"error": msg, "status": "failed", "email_id": email_id}

        except Exception as exc:
            msg = f"Unexpected Resend error: {exc}"
            log.error("resend.unexpected", email_id=email_id, error=msg)
            self._log_to_supabase(email_id, recipients, subject, "failed", None, tags, error=msg)
            return {"error": msg, "status": "failed", "email_id": email_id}

    def send_batch(self, emails: list[dict]) -> list[dict]:
        """
        Send multiple emails. Each dict must have: to, subject, html.
        Optional: text, from_name, reply_to, tags.
        Returns list of send results.
        """
        return [self.send(**e) for e in emails]

    # ── Internal ─────────────────────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=RETRY_MIN_S, max=RETRY_MAX_S),
        retry=retry_if_exception_type(requests.ConnectionError),
        reraise=True,
    )
    def _call_resend(self, payload: dict) -> str:
        """POST to Resend API. Returns the Resend email ID on success."""
        r = requests.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type":  "application/json",
            },
            json=payload,
            timeout=20,
        )
        if r.status_code in (200, 201):
            return r.json().get("id", "unknown")
        raise ResendError(f"Resend {r.status_code}: {r.text[:300]}")

    def _log_to_supabase(
        self,
        email_id:   str,
        recipients: list[str],
        subject:    str,
        status:     str,
        resend_id:  Optional[str],
        tags:       Optional[dict],
        error:      Optional[str] = None,
    ) -> None:
        """Log send attempt to Supabase email_log table. Non-fatal."""
        try:
            requests.post(
                f"{self._supa_url}/rest/v1/email_log",
                headers={
                    "apikey":        self._supa_key,
                    "Authorization": f"Bearer {self._supa_key}",
                    "Content-Type":  "application/json",
                    "Prefer":        "return=minimal",
                },
                json={
                    "id":         email_id,
                    "resend_id":  resend_id,
                    "recipients": recipients,
                    "subject":    subject,
                    "status":     status,
                    "error":      error,
                    "tags":       tags or {},
                    "sent_at":    datetime.now(timezone.utc).isoformat(),
                },
                timeout=10,
            )
        except Exception as exc:
            log.warning("resend.log_failed", error=str(exc))  # non-fatal
