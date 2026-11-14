"""Small shared helpers: ids, dates, text matching."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta, timezone


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> str:
    """All Continuum timestamps are stored as ISO-8601 UTC strings.

    Strings keep the demo portable across MongoDB, mongomock and JSON responses,
    and sort correctly.
    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today_iso() -> str:
    return date.today().isoformat()


def parse_date(value: str | date | datetime | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def add_days(base: str | date, days: int) -> str:
    d = parse_date(base) or date.today()
    return (d + timedelta(days=days)).isoformat()


def date_from_offset(text: str, anchor: str | date | None = None) -> str | None:
    """Pull a due date out of phrases like 'within 7 days' or 'in 2 weeks'."""
    anchor_date = parse_date(anchor) or date.today()
    lowered = text.lower()
    m = re.search(r"(?:within|in|after)\s+(\d{1,2})\s*(day|days|week|weeks|month|months)", lowered)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("week"):
            n *= 7
        elif unit.startswith("month"):
            n *= 30
        return (anchor_date + timedelta(days=n)).isoformat()
    m = re.search(r"(?:by|on)\s+((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2})", lowered)
    if m:
        for fmt in ("%B %d", "%b %d"):
            try:
                return datetime.strptime(m.group(1).replace(".", "").title(), fmt).replace(
                    year=anchor_date.year
                ).date().isoformat()
            except ValueError:
                continue
    return None


def extract_date(text: str, anchor: str | date | None = None) -> str | None:
    anchor_date = parse_date(anchor) or date.today()
    lowered = text.lower()
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", lowered)
    if m:
        return m.group(1)
    m = re.search(
        r"\b((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?)\b",
        lowered,
    )
    if m:
        raw = re.sub(r"(st|nd|rd|th)", "", m.group(1).replace(".", "")).title()
        for fmt in ("%B %d", "%b %d"):
            try:
                parsed = datetime.strptime(raw, fmt).replace(year=anchor_date.year)
                # If the parsed date is far in the past, assume next year.
                if (parsed.date() - anchor_date).days < -180:
                    parsed = parsed.replace(year=anchor_date.year + 1)
                return parsed.date().isoformat()
            except ValueError:
                continue
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", lowered)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else anchor_date.year
        if year < 100:
            year += 2000
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    return None


STOPWORDS = {
    "the", "a", "an", "and", "or", "with", "within", "for", "to", "of", "in", "on",
    "at", "by", "patient", "please", "appointment", "follow", "followup", "up", "his",
    "her", "their", "patient's", "she", "he", "they", "was", "is", "has", "had", "have",
    "been", "about", "after", "before", "day", "days", "week", "weeks", "month",
}


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if len(w) > 2 and w not in STOPWORDS}


def similarity(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def truncate(text: str, limit: int = 280) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}
# Part of day → local clock hour for the callback.
_PART_OF_DAY = {"morning": 9, "noon": 12, "midday": 12, "afternoon": 14, "evening": 18, "tonight": 18, "night": 18}


def _call_zone():
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    from .config import settings

    try:
        return ZoneInfo(settings.call_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def resolve_when(value: str | None, today: date) -> tuple[datetime | None, date | None]:
    """Interpret a check-in's ``scheduled_for``.

    Accepts ISO datetimes, ISO dates and loose phrases the voice agent may pass
    through: "today", "tomorrow morning", "Friday afternoon", "next Monday",
    "Nov 20", "11/20 evening". A weekday means its next occurrence after
    ``today``; a part of day sets a local time in ``CALL_TIMEZONE`` (morning
    9:00, afternoon 14:00, evening 18:00). Returns ``(datetime, date)``; both
    are ``None`` when the value cannot be understood.
    """
    if not value:
        return None, None
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if "T" in text or " " in text:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt, dt.date()
        return None, dt.date()
    except ValueError:
        pass
    lowered = text.lower()
    words = re.findall(r"[a-z]+", lowered)

    day: date | None = None
    if lowered.startswith(("now", "asap", "as soon as")) or "today" in words or "tonight" in words:
        day = today
    elif "tomorrow" in words:
        day = today + timedelta(days=1)
    else:
        weekday = next((_WEEKDAYS[w] for w in words if w in _WEEKDAYS), None)
        if weekday is not None:
            ahead = (weekday - today.weekday()) % 7 or 7  # next occurrence after today
            day = today + timedelta(days=ahead)
        else:
            day = parse_date(text) or parse_date(extract_date(text, today))
            if day is None and lowered.startswith("this "):
                day = today  # "this afternoon", "this week"

    if day is None:
        return None, None
    hour = next((_PART_OF_DAY[w] for w in words if w in _PART_OF_DAY), None)
    if hour is None:
        return None, day
    dt = datetime(day.year, day.month, day.day, hour, 0, tzinfo=_call_zone())
    return dt, day
