"""Outbound email: rendering + delivery backends.

Two backends, selected by ``Settings.email_backend``:

- ``console`` — prints the email to stdout. Zero-config default for local dev
  and tests; the clickable links appear in the API/worker logs.
- ``smtp`` — plain SMTP. Points at Mailpit in docker-compose and at any
  transactional provider (SES, Postmark, ...) in production.

Rendering is pure string building (no template engine): every email is the
same calm single-card layout with one call-to-action link. Subjects and body
copy are Turkish — product language — while code and comments stay English.
"""

import smtplib
from email.message import EmailMessage

from senthire.config import get_settings


def render_email(
    title: str,
    lines: list[str],
    cta_label: str,
    cta_url: str,
    footnote: str,
) -> tuple[str, str]:
    """Return (html, text) for the standard single-card layout."""
    paragraphs_html = "".join(
        f'<p style="margin:0 0 14px;color:#3d4540;font-size:15px;line-height:1.6;">{line}</p>'
        for line in lines
    )
    html = f"""\
<div style="background:#f6f7f4;padding:32px 16px;font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif;">
  <div style="max-width:480px;margin:0 auto;">
    <div style="font-size:17px;font-weight:700;color:#202723;margin:0 0 16px;">
      sent<span style="color:#177e71;">Hire</span>
    </div>
    <div style="background:#ffffff;border:1px solid #e4e7e0;border-radius:12px;padding:28px;">
      <h1 style="font-size:19px;font-weight:650;color:#202723;margin:0 0 12px;">{title}</h1>
      {paragraphs_html}
      <a href="{cta_url}"
         style="display:inline-block;background:#177e71;color:#ffffff;text-decoration:none;
                font-size:15px;font-weight:600;padding:11px 22px;border-radius:8px;margin:6px 0 18px;">
        {cta_label}</a>
      <p style="margin:0 0 6px;color:#8a948c;font-size:13px;line-height:1.5;">
        Düğme çalışmazsa bu bağlantıyı tarayıcınıza yapıştırın:</p>
      <p style="margin:0;word-break:break-all;font-size:13px;">
        <a href="{cta_url}" style="color:#177e71;">{cta_url}</a></p>
    </div>
    <p style="color:#8a948c;font-size:12.5px;line-height:1.5;margin:14px 4px 0;">{footnote}</p>
  </div>
</div>"""
    text = "\n".join(
        [title, "", *lines, "", f"{cta_label}: {cta_url}", "", footnote]
    )
    return html, text


def invitation_email(
    org_name: str, inviter_name: str, invite_url: str, expires_days: int
) -> tuple[str, str, str]:
    """Return (subject, html, text) for a workspace invitation."""
    subject = f"{org_name} sizi sentHire'a davet ediyor"
    html, text = render_email(
        title=f"{org_name} ekibine katılın",
        lines=[
            f"<strong>{inviter_name}</strong>, sizi <strong>{org_name}</strong> "
            "çalışma alanına davet etti.",
            "Katıldığınızda ekibinizle aynı ilanları, adayları ve değerlendirme "
            "sonuçlarını görürsünüz.",
        ],
        cta_label="Daveti kabul et",
        cta_url=invite_url,
        footnote=(
            f"Bu bağlantı {expires_days} gün geçerlidir ve yalnızca bir kez "
            "kullanılabilir. Daveti siz beklemiyorsanız bu e-postayı yok sayabilirsiniz."
        ),
    )
    return subject, html, text


def password_reset_email(reset_url: str, ttl_minutes: int) -> tuple[str, str, str]:
    """Return (subject, html, text) for a password reset."""
    subject = "sentHire şifrenizi sıfırlayın"
    html, text = render_email(
        title="Şifrenizi sıfırlayın",
        lines=[
            "Hesabınız için bir şifre sıfırlama isteği aldık.",
            "Yeni şifrenizi belirlemek için aşağıdaki düğmeye tıklayın.",
        ],
        cta_label="Yeni şifre belirle",
        cta_url=reset_url,
        footnote=(
            f"Bu bağlantı {ttl_minutes} dakika geçerlidir ve yalnızca bir kez "
            "kullanılabilir. Bu isteği siz yapmadıysanız bu e-postayı yok sayın — "
            "şifreniz değişmez."
        ),
    )
    return subject, html, text


def send_email(to: str, subject: str, html: str, text: str) -> None:
    """Deliver via the configured backend. Raises on SMTP failure (caller retries)."""
    settings = get_settings()
    if settings.email_backend == "smtp":
        message = EmailMessage()
        message["From"] = settings.email_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text)
        message.add_alternative(html, subtype="html")
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password or "")
            smtp.send_message(message)
        return
    # console backend: make links clickable straight from the logs
    print(
        "\n".join(
            [
                "-- email (console backend) " + "-" * 40,
                f"To: {to}",
                f"Subject: {subject}",
                "",
                text,
                "-" * 67,
            ]
        )
    )
