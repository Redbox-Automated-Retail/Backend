"""Helpers for the JSON shapes the .NET clients produce and expect."""
import datetime as _dt

_EPOCH_TICKS = 621355968000000000  # DateTime(1970,1,1).Ticks


def utcnow():
    return _dt.datetime.now(_dt.timezone.utc)


def redbox_datetime(value=None, kind="Utc"):
    """Redbox.Core DateTimeConverter object: {"theDate": ticks, "dateTimeKind": ..., "dateString": ...}."""
    if value is None:
        value = utcnow()
    if value.tzinfo is not None:
        value = value.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    delta = value - _dt.datetime(1970, 1, 1)
    ticks = _EPOCH_TICKS + (delta.days * 86400 + delta.seconds) * 10_000_000 + delta.microseconds * 10
    s = value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond * 10:07d}"
    if kind == "Utc":
        s += "Z"
    return {"theDate": ticks, "dateTimeKind": kind, "dateString": s}


def parse_redbox_datetime(value):
    """Accept a Redbox.Core date object, an ISO string or None."""
    if value is None:
        return None
    if isinstance(value, dict):
        if value.get("dateString"):
            return parse_iso(value["dateString"])
        ticks = value.get("theDate")
        if ticks is not None:
            return _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc) + _dt.timedelta(
                microseconds=(int(ticks) - _EPOCH_TICKS) / 10)
        return None
    return parse_iso(str(value))


def parse_iso(s):
    if not s:
        return None
    s = s.strip()
    if s.startswith("/Date("):  # MS AJAX date
        ms = int(s[6:].split(")")[0].split("+")[0].split("-")[0])
        return _dt.datetime.fromtimestamp(ms / 1000, _dt.timezone.utc)
    s = s.replace("Z", "+00:00")
    if "." in s:  # .NET writes up to 7 fractional digits
        head, frac = s.split(".", 1)
        tz = ""
        for sep in ("+", "-"):
            if sep in frac:
                frac, tz = frac.split(sep, 1)
                tz = sep + tz
                break
        s = f"{head}.{frac[:6].ljust(6, '0')}{tz}"
    try:
        return _dt.datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%m/%d/%Y %H:%M:%S"):
            try:
                return _dt.datetime.strptime(s, fmt)
            except ValueError:
                pass
    return None


def iso(value=None, utc=True):
    """Newtonsoft-style ISO-8601 string."""
    if value is None:
        value = utcnow()
    if utc:
        if value.tzinfo is not None:
            value = value.astimezone(_dt.timezone.utc)
        return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond:06d}0Z"
    return value.isoformat()


def ticks_from_unix(seconds: float) -> int:
    return _EPOCH_TICKS + int(seconds * 10_000_000)
