"""Dates and times as profile.data stores them, from what the dashboard sends.

profile.data dates are "yyyyMMddHHmmss" (the engine reads them with ParseExact; anything else drops the row) and a
store's return time is "hh:mmtt" ("09:00PM"). The dashboard's pickers send ISO dates ("2026-01-31",
"2026-01-31T00:00:00Z") and 24-hour times ("21:00"); these turn either form into the stored one, and back.
"""
import datetime as dt
import re


class DateError(ValueError):
    pass


def to_redbox_date(value, field="date"):
    """ISO date / timestamp, yyyyMMdd or yyyyMMddHHmmss -> "yyyyMMddHHmmss"; empty -> None."""
    if value in (None, ""):
        return None
    if isinstance(value, (dt.date, dt.datetime)):
        d = value
    else:
        s = str(value).strip()
        if re.fullmatch(r"\d{8}(\d{6})?", s):
            s = s + "000000" if len(s) == 8 else s
            try:
                dt.datetime.strptime(s, "%Y%m%d%H%M%S")
            except ValueError:
                raise DateError(f"{field}: {value!r} is not a real date")
            return s
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            raise DateError(f"{field}: {value!r} is not a date")
    if isinstance(d, dt.datetime):
        return d.strftime("%Y%m%d%H%M%S")
    return d.strftime("%Y%m%d") + "000000"


def from_redbox_date(value):
    """"yyyyMMdd[HHmmss]" -> ISO date "yyyy-mm-dd" (None when empty or unreadable)."""
    s = "".join(c for c in str(value or "") if c.isdigit())
    if len(s) < 8:
        return None
    try:
        return dt.datetime.strptime(s[:8], "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def to_redbox_time(value, field="time"):
    """"21:00", "9:00 PM" or "09:00PM" -> "09:00PM"; empty -> None."""
    if value in (None, ""):
        return None
    s = str(value).strip().upper().replace(" ", "")
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M%p", "%I%p"):
        try:
            return dt.datetime.strptime(s, fmt).strftime("%I:%M%p")
        except ValueError:
            continue
    raise DateError(f"{field}: {value!r} is not a time")


def from_redbox_time(value):
    """"09:00PM" -> "21:00" (None when empty or unreadable)."""
    try:
        return dt.datetime.strptime(to_redbox_time(value), "%I:%M%p").strftime("%H:%M") if value else None
    except DateError:
        return None


def to_display_time(value, field="time"):
    """"21:00" -> "9:00 PM" (the kiosk installer's due time)."""
    t = to_redbox_time(value, field)
    return None if t is None else dt.datetime.strptime(t, "%I:%M%p").strftime("%I:%M %p").lstrip("0")
