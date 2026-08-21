"""Email delivery task: keeps SMTP latency and outages out of API requests.

The API renders the email (pure string work) and enqueues delivery here. The
task retries transient SMTP/network failures with backoff. Note the rendered
body carries the raw invitation/reset link, so it transits the broker — Redis
is internal infrastructure, and the links are single-use and expiring.
"""

import smtplib

from senthire.services.email import send_email
from senthire.workers.celery_app import celery_app


@celery_app.task(
    name="senthire.mail.send",
    autoretry_for=(smtplib.SMTPException, ConnectionError, OSError, TimeoutError),
    retry_backoff=10,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=6,
)
def send_mail(
    to: str,
    subject: str,
    html: str,
    text: str,
    reply_to: str | None = None,
    ics: str | None = None,
) -> dict:
    send_email(to, subject, html, text, reply_to=reply_to, ics=ics)
    return {"to": to, "subject": subject}


def enqueue_mail(
    to: str,
    subject: str,
    html: str,
    text: str,
    reply_to: str | None = None,
    ics: str | None = None,
) -> bool:
    """Best-effort enqueue from API handlers.

    Returns False instead of raising when the broker is unreachable — the
    caller still has the raw link to show, so the request must not fail.
    """
    try:
        send_mail.delay(to, subject, html, text, reply_to, ics)
        return True
    except Exception:
        return False
