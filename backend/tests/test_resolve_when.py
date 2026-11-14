from datetime import date, datetime

import pytest

from app.config import settings
from app.util import resolve_when

TODAY = date(2026, 11, 14)  # a Saturday


@pytest.mark.parametrize(
    "text, day, hour",
    [
        ("Friday afternoon", date(2026, 11, 20), 14),
        ("Monday morning", date(2026, 11, 16), 9),
        ("friday", date(2026, 11, 20), None),
        ("next Friday", date(2026, 11, 20), None),
        ("Saturday", date(2026, 11, 21), None),  # a weekday is the next occurrence *after* today
        ("tomorrow morning", date(2026, 11, 15), 9),
        ("tomorrow", date(2026, 11, 15), None),
        ("today", TODAY, None),
        ("this evening", TODAY, 18),
        ("this week", TODAY, None),
        ("Nov 20", date(2026, 11, 20), None),
        ("Nov 20 evening", date(2026, 11, 20), 18),
        ("2026-11-20", date(2026, 11, 20), None),
    ],
)
def test_loose_callback_times(text, day, hour):
    dt, d = resolve_when(text, TODAY)
    assert d == day
    if hour is None:
        assert dt is None
    else:
        assert (dt.hour, dt.minute) == (hour, 0) and dt.date() == day
        assert str(dt.tzinfo) == settings.call_timezone


def test_iso_datetime_is_kept():
    dt, d = resolve_when("2026-11-20T10:00:00Z", TODAY)
    assert dt == datetime.fromisoformat("2026-11-20T10:00:00+00:00") and d == date(2026, 11, 20)


@pytest.mark.parametrize("text", ["", None, "whenever works", "sometime soon-ish"])
def test_unparseable_times(text):
    assert resolve_when(text, TODAY) == (None, None)
