from datetime import datetime, timezone

from freezegun import freeze_time

from superpal.schedule import next_sunday_noon_utc


def _utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# Wednesday mid-afternoon — next Sunday noon is 4 days away
@freeze_time("2026-05-13 15:00:00+00:00")
def test_wednesday_returns_next_sunday():
    result = next_sunday_noon_utc()
    assert result == _utc(2026, 5, 17, 12, 0)


# Sunday before noon — same-day noon is still in the future
@freeze_time("2026-05-17 10:00:00+00:00")
def test_sunday_before_noon_returns_today():
    result = next_sunday_noon_utc()
    assert result == _utc(2026, 5, 17, 12, 0)


# Sunday at noon exactly — that moment is not in the future, advance one week
@freeze_time("2026-05-17 12:00:00+00:00")
def test_sunday_at_noon_returns_next_week():
    result = next_sunday_noon_utc()
    assert result == _utc(2026, 5, 24, 12, 0)


# Sunday after noon — advance one week
@freeze_time("2026-05-17 14:00:00+00:00")
def test_sunday_after_noon_returns_next_week():
    result = next_sunday_noon_utc()
    assert result == _utc(2026, 5, 24, 12, 0)


# Result is always UTC-aware
@freeze_time("2026-05-13 15:00:00+00:00")
def test_result_is_utc_aware():
    result = next_sunday_noon_utc()
    assert result.tzinfo == timezone.utc


# Result is always a future datetime
@freeze_time("2026-05-13 15:00:00+00:00")
def test_result_is_in_the_future():
    now = datetime.now(timezone.utc)
    assert next_sunday_noon_utc() > now
