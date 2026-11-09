from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from services.schedule import allowed, validate
from handlers.schedule import parse_window


def row(**changes):
    return SimpleNamespace(**(dict(enabled=True, timezone="UTC", days=[0], start_minute=540, end_minute=1020) | changes))


@pytest.mark.parametrize("instant, expected", [
    ("2026-11-09T08:59:59+00:00", False), ("2026-11-09T09:00:00+00:00", True),
    ("2026-11-09T16:59:59+00:00", True), ("2026-11-09T17:00:00+00:00", False),
    ("2026-11-10T12:00:00+00:00", False)])
def test_boundaries_and_weekdays(instant, expected):
    assert allowed(row(), datetime.fromisoformat(instant)) is expected


@pytest.mark.parametrize("instant, expected", [
    ("2026-11-08T21:59:00+00:00", False), ("2026-11-08T22:00:00+00:00", True),
    ("2026-11-09T01:59:00+00:00", True), ("2026-11-09T02:00:00+00:00", False),
    ("2026-11-09T22:00:00+00:00", False)])
def test_overnight_week_rollover(instant, expected):
    assert allowed(row(days=[6], start_minute=1320, end_minute=120), datetime.fromisoformat(instant)) is expected


def test_explicit_timezone_and_dst_both_repeated_hours():
    tehran = row(timezone="Asia/Tehran")
    assert allowed(tehran, datetime(2026, 11, 9, 5, 30, tzinfo=timezone.utc))  # 09:00 Tehran
    berlin = row(timezone="Europe/Berlin", days=[6], start_minute=120, end_minute=180)
    # Both occurrences of 02:30 during the autumn clock change are in local hours.
    assert allowed(berlin, datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc))
    assert allowed(berlin, datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc))
    assert not allowed(berlin, datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc))  # skipped 02:30


def test_default_disabled_corrupt_settings_and_full_day():
    assert allowed(None)
    assert allowed(row(enabled=False, timezone="invalid"))
    assert not allowed(row(timezone="invalid"))
    assert not allowed(row(days=[]))
    assert not allowed(row(), datetime(2026, 11, 9, 12))  # naive instant rejected
    assert allowed(row(start_minute=0, end_minute=1440), datetime(2026, 11, 9, 23, 59, tzinfo=timezone.utc))


@pytest.mark.parametrize("value, result", [("09:00-17:00", (540, 1020)), ("۲۲:۰۰-۰۲:۰۰", (1320, 120)),
                                           ("00:00-24:00", (0, 1440))])
def test_parse(value, result):
    assert parse_window(value) == result


@pytest.mark.parametrize("value", ["24:00-02:00", "09:60-17:00", "09:00-09:00", "hello", "09:00-24:01"])
def test_invalid_input(value):
    with pytest.raises(ValueError):
        parse_window(value)
