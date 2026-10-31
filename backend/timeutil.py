"""Time helpers. Everything is stored in UTC and converted for display/rules."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def now_utc():
    # Always call as timeutil.now_utc() so tests can substitute the clock.
    return datetime.now(UTC).replace(microsecond=0)


def to_iso(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def parse_iso(value):
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def zone(name):
    return ZoneInfo(name)


def local_date(dt, tz):
    return dt.astimezone(tz).date()


def local_today(tz):
    return local_date(now_utc(), tz)


def combine_local(day, hhmm, tz):
    """A wall-clock time on a business date, as a UTC datetime."""
    hour, minute = map(int, hhmm.split(":"))
    return datetime.combine(day, time(hour, minute), tz).astimezone(UTC)


def parse_local_datetime(value, tz):
    """'YYYY-MM-DDTHH:MM[:SS]' in the business timezone -> UTC datetime."""
    return datetime.fromisoformat(value).replace(tzinfo=tz, microsecond=0).astimezone(UTC)


def format_local_datetime(iso, tz):
    dt = parse_iso(iso)
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def format_clock(dt, tz, time_format="12"):
    local = dt.astimezone(tz)
    if str(time_format) == "24":
        return local.strftime("%H:%M:%S")
    return local.strftime("%I:%M:%S %p")


def format_hhmm(dt, tz, time_format="12"):
    local = dt.astimezone(tz)
    if str(time_format) == "24":
        return local.strftime("%H:%M")
    return local.strftime("%I:%M %p")


def human_duration(seconds):
    minutes = int(seconds or 0) // 60
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def date_range(start, end):
    day = date.fromisoformat(start)
    last = date.fromisoformat(end)
    while day <= last:
        yield day.isoformat()
        day += timedelta(days=1)
