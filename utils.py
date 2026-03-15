from datetime import datetime, date, timedelta


def _friendly_date(d: date) -> str:
    today = date.today()
    if d == today:
        return "today"
    elif d == today + timedelta(days=1):
        return "tomorrow"
    elif today < d <= today + timedelta(days=6):
        return "this " + d.strftime("%A")
    elif today + timedelta(days=6) < d <= today + timedelta(days=13):
        return "next " + d.strftime("%A")
    elif d.year == today.year:
        return d.strftime("%B %-d")
    else:
        return d.strftime("%B %-d, %Y")


def _friendly_dt(dt: datetime) -> str:
    return f"{_friendly_date(dt.date())} at {dt.strftime('%H:%M')}"


def _friendly_rrule(rules: list[str]) -> str:
    """Convert a list of RRULE strings into a human-readable recurrence description."""
    _DAY_NAMES = {"MO": "Monday", "TU": "Tuesday", "WE": "Wednesday",
                  "TH": "Thursday", "FR": "Friday", "SA": "Saturday", "SU": "Sunday"}
    _WEEKDAYS = {"MO", "TU", "WE", "TH", "FR"}
    _WEEKEND = {"SA", "SU"}

    for rule in rules:
        if not rule.startswith("RRULE:"):
            continue
        parts = dict(p.split("=", 1) for p in rule[6:].split(";"))
        freq = parts.get("FREQ", "")
        interval = int(parts.get("INTERVAL", 1))
        count = parts.get("COUNT")
        until = parts.get("UNTIL")
        byday = parts.get("BYDAY", "")
        bymonthday = parts.get("BYMONTHDAY", "")

        if freq == "DAILY":
            result = "every day" if interval == 1 else f"every {interval} days"
        elif freq == "WEEKLY":
            if byday:
                days = set(byday.split(","))
                if days == _WEEKDAYS:
                    day_str = "weekday"
                elif days == _WEEKEND:
                    day_str = "weekend day"
                else:
                    day_str = ", ".join(_DAY_NAMES.get(d, d) for d in byday.split(","))
                result = f"every {day_str}" if interval == 1 else f"every {interval} weeks on {day_str}"
            else:
                result = "every week" if interval == 1 else f"every {interval} weeks"
        elif freq == "MONTHLY":
            if bymonthday:
                n = int(bymonthday)
                suffix = {1: "st", 2: "nd", 3: "rd"}.get(n if n <= 3 else n % 10 if n % 10 <= 3 and n not in (11, 12, 13) else 0, "th")
                day_str = f"the {n}{suffix}"
                result = f"every month on {day_str}" if interval == 1 else f"every {interval} months on {day_str}"
            else:
                result = "every month" if interval == 1 else f"every {interval} months"
        elif freq == "YEARLY":
            result = "every year" if interval == 1 else f"every {interval} years"
        else:
            return rule

        if count:
            result += f", {count} times"
        elif until:
            try:
                result += f" until {_friendly_date(datetime.strptime(until[:8], '%Y%m%d').date())}"
            except ValueError:
                pass
        return result
    return ", ".join(rules)


def _friendly_event_time(dt_dict: dict) -> str:
    """Parse a Calendar API start/end dict and return a friendly label."""
    if "dateTime" in dt_dict:
        return _friendly_dt(datetime.fromisoformat(dt_dict["dateTime"]))
    return _friendly_date(date.fromisoformat(dt_dict["date"]))
