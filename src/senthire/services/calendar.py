"""iCalendar invitations, built by hand on purpose.

The interview invitation carries a .ics attachment so the candidate lands the
meeting in their calendar with one tap. RFC 5545 for a single VEVENT is ~20
lines; a dependency for that trades one page of code we control for someone
else's release cycle.

Times: recruiters type "25.08.2026 14:00" meaning wall-clock Istanbul time.
The event is emitted with TZID=Europe/Istanbul and an embedded VTIMEZONE
(fixed +03, no DST since 2016), so every calendar client shows the hour the
recruiter meant — a bare UTC timestamp would shift the meeting for anyone
whose client second-guesses timezones.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta

TZID = "Europe/Istanbul"

# "25.08.2026 14:00" — the format the composer suggests. Tolerant about the
# separator, strict about the meaning: day first, as Turkish dates are written.
_WHEN = re.compile(
    r"^\s*(\d{1,2})[./](\d{1,2})[./](\d{4})\s+(\d{1,2})[:.](\d{2})\s*$"
)


def parse_when(text: str | None) -> datetime | None:
    """The recruiter's free-text meeting time, if it is unambiguous."""
    if not text:
        return None
    match = _WHEN.match(text)
    if not match:
        return None
    day, month, year, hour, minute = (int(g) for g in match.groups())
    try:
        return datetime(year, month, day, hour, minute)  # naive = Istanbul wall clock
    except ValueError:
        return None


def _escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\").replace(";", "\;").replace(",", "\\,").replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """RFC 5545 §3.1: lines over 75 octets are folded with CRLF + space."""
    out = []
    data = line.encode("utf-8")
    while len(data) > 73:
        cut = 73
        while cut > 0 and (data[cut] & 0xC0) == 0x80:  # do not split a UTF-8 char
            cut -= 1
        out.append(data[:cut].decode("utf-8"))
        data = b" " + data[cut:]
    out.append(data.decode("utf-8"))
    return "\r\n".join(out)


def interview_ics(
    *,
    summary: str,
    starts_at: datetime,
    duration_minutes: int = 60,
    organizer_name: str,
    organizer_email: str,
    attendee_email: str,
    description: str | None = None,
    uid: str | None = None,
) -> str:
    """One VEVENT, REQUEST method, so clients offer Accept/Decline."""
    start = starts_at.strftime("%Y%m%dT%H%M%S")
    end = (starts_at + timedelta(minutes=duration_minutes)).strftime("%Y%m%dT%H%M%S")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//sentHire//outreach//TR",
        "METHOD:REQUEST",
        "BEGIN:VTIMEZONE",
        f"TZID:{TZID}",
        "BEGIN:STANDARD",
        "DTSTART:20161030T040000",
        "TZOFFSETFROM:+0300",
        "TZOFFSETTO:+0300",
        "TZNAME:+03",
        "END:STANDARD",
        "END:VTIMEZONE",
        "BEGIN:VEVENT",
        f"UID:{uid or uuid.uuid4()}@senthire",
        f"DTSTAMP:{stamp}",
        f"DTSTART;TZID={TZID}:{start}",
        f"DTEND;TZID={TZID}:{end}",
        f"SUMMARY:{_escape(summary)}",
        f'ORGANIZER;CN={_escape(organizer_name)}:mailto:{organizer_email}',
        f"ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{attendee_email}",
        "STATUS:CONFIRMED",
    ]
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
