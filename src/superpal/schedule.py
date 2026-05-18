from datetime import datetime, timedelta, timezone


def next_sunday_noon_utc() -> datetime:
    """Return the next Sunday noon UTC that is strictly in the future."""
    now = datetime.now(timezone.utc)
    days_since_sunday = (now.weekday() + 1) % 7
    this_sunday = now.date() - timedelta(days=days_since_sunday)
    candidate = datetime(
        this_sunday.year,
        this_sunday.month,
        this_sunday.day,
        12,
        0,
        tzinfo=timezone.utc,
    )
    if now >= candidate:
        candidate += timedelta(weeks=1)
    return candidate
