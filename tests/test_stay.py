"""Séjours : fenêtre, statut, pause nocturne, nombre de photos (module pur)."""
from __future__ import annotations

from datetime import date, datetime, time

from homeassistant.util import dt as dt_util

from custom_components.immich_guest_slideshow.stay import (
    FREE,
    ONGOING,
    PLANNED,
    format_stay,
    frame_photo_count,
    parse_quiet,
    quiet_remaining,
    stay_status,
    stay_window,
)


def _tz():
    return dt_util.get_default_time_zone()


def test_window_all_day_saturday_to_sunday_ends_monday_0001() -> None:
    """Saisi « journée entière » samedi → dimanche (HA stocke la fin au lundi) :
    du samedi 9 h au lundi 00:01."""
    start, end = stay_window("2026-10-24", "2026-10-26", 9)
    assert start == datetime(2026, 10, 24, 9, 0, tzinfo=_tz())
    assert end == datetime(2026, 10, 26, 0, 1, tzinfo=_tz())


def test_window_timed_event() -> None:
    start, end = stay_window(
        datetime(2026, 10, 24, 16, 0, tzinfo=_tz()),
        datetime(2026, 10, 25, 11, 0, tzinfo=_tz()),
        9,
    )
    assert start == datetime(2026, 10, 24, 9, 0, tzinfo=_tz())
    assert end == datetime(2026, 10, 26, 0, 1, tzinfo=_tz())


def test_status_free_planned_ongoing() -> None:
    events = [("2026-10-24", "2026-10-26", "Alexia")]
    monday = stay_status(events, datetime(2026, 10, 19, 19, 0, tzinfo=_tz()), 9)
    assert monday.state == PLANNED and not monday.active
    assert monday.next_start == datetime(2026, 10, 24, 9, 0, tzinfo=_tz())
    assert monday.label == "sam. 24 → dim. 25 oct."
    assert monday.names == ("Alexia",)

    assert stay_status(events, datetime(2026, 10, 24, 8, 59, tzinfo=_tz()), 9).state == PLANNED
    assert stay_status(events, datetime(2026, 10, 24, 9, 0, tzinfo=_tz()), 9).state == ONGOING
    assert stay_status(events, datetime(2026, 10, 26, 0, 0, tzinfo=_tz()), 9).state == ONGOING
    after = stay_status(events, datetime(2026, 10, 26, 0, 1, tzinfo=_tz()), 9)
    assert after.state == FREE and after.next_start is None


def test_next_stay_far_in_the_future_is_planned() -> None:
    events = [("2027-03-06", "2027-03-08", "Bruno")]
    status = stay_status(events, datetime(2026, 10, 21, 12, 0, tzinfo=_tz()), 9)
    assert status.state == PLANNED and status.days == (date(2027, 3, 6), date(2027, 3, 7))


def test_one_event_per_guest_forms_one_stay() -> None:
    events = [
        ("2026-10-23", "2026-10-25", "Corinne Jacquouton"),
        ("2026-10-23", "2026-10-25", "Regis Jacquouton"),
        ("2026-11-06", "2026-11-08", "Autre"),
    ]
    status = stay_status(events, datetime(2026, 10, 19, 19, 0, tzinfo=_tz()), 9)
    assert status.names == ("Corinne Jacquouton", "Regis Jacquouton")
    assert status.days == (date(2026, 10, 23), date(2026, 10, 24))


def test_reset_ends_the_current_stay_only() -> None:
    events = [("2026-10-24", "2026-10-26"), ("2026-10-30", "2026-11-01")]
    reset = datetime(2026, 10, 25, 15, 0, tzinfo=_tz())
    sunday = stay_status(events, datetime(2026, 10, 25, 18, 0, tzinfo=_tz()), 9, reset)
    assert sunday.state == PLANNED
    assert sunday.next_start == datetime(2026, 10, 30, 9, 0, tzinfo=_tz())
    friday = datetime(2026, 10, 30, 10, 0, tzinfo=_tz())
    assert stay_status(events, friday, 9, closed_at=reset).active


def test_format_stay_french() -> None:
    assert format_stay(date(2026, 10, 23), date(2026, 10, 24)) == "ven. 23 → sam. 24 oct."
    assert format_stay(date(2026, 10, 30), date(2026, 11, 2)) == "ven. 30 oct. → lun. 2 nov."
    assert format_stay(date(2026, 10, 24), date(2026, 10, 24)) == "sam. 24 oct."


def test_parse_quiet() -> None:
    assert parse_quiet("23:00-07:00") == (time(23, 0), time(7, 0))
    assert parse_quiet(" 1:30 - 6:00 ") == (time(1, 30), time(6, 0))
    assert parse_quiet("") is None
    assert parse_quiet("25:00-07:00") is None
    assert parse_quiet("07:00-07:00") is None


def test_quiet_remaining_across_midnight() -> None:
    quiet = parse_quiet("23:00-07:00")
    assert quiet_remaining(datetime(2026, 10, 18, 23, 30, tzinfo=_tz()), quiet) == 7.5 * 3600
    assert quiet_remaining(datetime(2026, 10, 19, 6, 0, tzinfo=_tz()), quiet) == 3600
    assert quiet_remaining(datetime(2026, 10, 19, 12, 0, tzinfo=_tz()), quiet) is None
    assert quiet_remaining(datetime(2026, 10, 19, 12, 0, tzinfo=_tz()), None) is None


def test_photo_count_one_per_hour_without_quiet_hours() -> None:
    """Samedi 9 h → lundi 00:01 : 40 réveils horaires (samedi 9 h…23 h, tout
    dimanche, lundi 0 h). Pause 01:00-07:00 la nuit de samedi : 40 − 6 = 34."""
    window = stay_window("2026-10-24", "2026-10-26", 9)
    assert frame_photo_count(window, 3600, None, 500) == 40  # 9 h … 00 h, + 00:00
    assert frame_photo_count(window, 3600, parse_quiet("01:00-07:00"), 500) == 34
    assert frame_photo_count(window, 3600, None, 10) == 10  # plafond
