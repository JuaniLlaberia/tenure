"""
Send times the founder picks by hand: the ⏰ presets and times typed like "Fri 18:00",
"tomorrow 9am" or "16 Oct 9:30". Plain code, read in the business's timezone.
"""

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
CLOCK = re.compile(r"\b(\d{1,2})(?:[:.h](\d{2}))?\s*(am|pm)?\b")
IN = re.compile(r"\bin\s+(\d+)\s*(min|minute|minutes|h|hr|hrs|hour|hours)\b")
DAY_MONTH = re.compile(r"\b(\d{1,2})\s+([a-z]{3})[a-z]*\b")
MONTH_DAY = re.compile(r"\b([a-z]{3})[a-z]*\s+(\d{1,2})\b")
DEFAULT_HOUR = 9

def preset(key: str, now: datetime, timezone: str) -> datetime | None:
    """
    "hour": in one hour; "tomorrow": tomorrow at 9:00; "monday": next Monday at 9:00.
    """
    zone = ZoneInfo(timezone)
    local = now.astimezone(zone)
    if key == "hour":
        return (now + timedelta(hours=1)).replace(second=0, microsecond=0)
    if key == "tomorrow":
        day = local + timedelta(days=1)
    elif key == "monday":
        day = local + timedelta(days=(7 - local.weekday()) or 7)
    else:
        return None
    return day.replace(hour=DEFAULT_HOUR, minute=0, second=0, microsecond=0).astimezone(UTC)

def parse(value: str, now: datetime, timezone: str) -> datetime | None:
    """
    A time typed by the founder, as UTC. None when it can't be read. A day without a time is
    9:00; a time without a day is the next time that clock comes round.
    """
    words = value.strip().lower()
    if not words:
        return None
    relative = IN.search(words)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        step = timedelta(minutes=amount) if unit.startswith("min") else timedelta(hours=amount)
        return (now + step).replace(second=0, microsecond=0)
    zone = ZoneInfo(timezone)
    local = now.astimezone(zone)
    day, rest, weekday = _day(words, local)
    clock = _clock(rest)
    if day is None and clock is None:
        return None
    hour, minute = clock or (DEFAULT_HOUR, 0)
    if day is None:
        moment = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if moment <= local:
            moment += timedelta(days=1)
        return moment.astimezone(UTC)
    moment = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if weekday and moment <= local:
        moment += timedelta(days=7)
    return moment.astimezone(UTC)

def _day(words: str, local: datetime) -> tuple[datetime | None, str, bool]:
    """
    The day named in the words, the words left for the clock, and whether it was a weekday.
    """
    if "today" in words:
        return local, words.replace("today", " "), False
    if "tomorrow" in words:
        return local + timedelta(days=1), words.replace("tomorrow", " "), False
    for found in (DAY_MONTH.search(words), MONTH_DAY.search(words)):
        if found is None:
            continue
        first, second = found.groups()
        day_text, month_text = (first, second) if first.isdigit() else (second, first)
        if month_text[:3] not in MONTHS:
            continue
        month = MONTHS.index(month_text[:3]) + 1
        try:
            day = local.replace(month=month, day=int(day_text))
        except ValueError:
            return None, words, False
        if day.date() < local.date():
            day = day.replace(year=day.year + 1)
        return day, words[: found.start()] + " " + words[found.end() :], False
    for index, name in enumerate(WEEKDAYS):
        found = re.search(rf"\b{name}[a-z]*\b", words)
        if found:
            ahead = (index - local.weekday()) % 7
            rest = words[: found.start()] + " " + words[found.end() :]
            return local + timedelta(days=ahead), rest, True
    return None, words, False

def _clock(words: str) -> tuple[int, int] | None:
    found = CLOCK.search(words)
    if found is None:
        return None
    hour, minute, half = int(found.group(1)), int(found.group(2) or 0), found.group(3)
    if half == "pm" and hour < 12:
        hour += 12
    if half == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return hour, minute
