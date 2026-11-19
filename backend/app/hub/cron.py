"""Schedules for automations: standard 5-field cron ("30 8 * * 1-5") and shorthands (@hourly, @daily, @weekly,
"@every 30m"). Times are evaluated in the automation's time zone."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MIN_INTERVAL_MIN = 5
SHORTHANDS = {"@hourly": "0 * * * *", "@daily": "0 9 * * *", "@weekly": "0 9 * * 1", "@weekdays": "0 9 * * 1-5",
              "@monthly": "0 9 1 * *"}
RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]


class CronError(ValueError):
    pass


def _field(text: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in text.split(","):
        m = re.fullmatch(r"(\*|\d+(?:-\d+)?)(?:/(\d+))?", part.strip())
        if not m:
            raise CronError(f"Invalid cron field '{part}'")
        rng, step = m.group(1), int(m.group(2) or 1)
        if step < 1:
            raise CronError("Step must be at least 1")
        if rng == "*":
            a, b = lo, hi
        elif "-" in rng:
            a, b = map(int, rng.split("-"))
        else:
            a = b = int(rng)
            if m.group(2):
                b = hi
        if hi == 6 and b == 7:  # allow 7 = Sunday
            b = 6 if a <= 6 else 0
            out.add(0)
        if not (lo <= a <= hi and lo <= b <= hi and a <= b):
            raise CronError(f"Value out of range in '{part}' (allowed {lo}-{hi})")
        out.update(range(a, b + 1, step))
    return out


class Schedule:
    def __init__(self, expr: str, tz: str = "UTC"):
        expr = (expr or "").strip()
        try:
            self.tz = ZoneInfo(tz or "UTC")
        except (ZoneInfoNotFoundError, ValueError):
            raise CronError(f"Unknown time zone '{tz}'")
        self.expr = expr
        self.every: timedelta | None = None
        m = re.fullmatch(r"@every\s+(\d+)\s*(m|min|minutes?|h|hours?|d|days?)", expr, re.I)
        if m:
            n, unit = int(m.group(1)), m.group(2)[0].lower()
            self.every = timedelta(minutes=n) if unit == "m" else timedelta(hours=n) if unit == "h" else timedelta(days=n)
            if self.every < timedelta(minutes=MIN_INTERVAL_MIN):
                raise CronError(f"The shortest interval is {MIN_INTERVAL_MIN} minutes")
            return
        expr = SHORTHANDS.get(expr.lower(), expr)
        parts = expr.split()
        if len(parts) != 5:
            raise CronError("Use 5 cron fields (minute hour day month weekday), e.g. '30 8 * * 1-5', or @daily / @every 30m")
        self.minutes, self.hours, self.days, self.months, self.weekdays = (
            _field(p, lo, hi) for p, (lo, hi) in zip(parts, RANGES))
        self.dom_any, self.dow_any = parts[2] == "*", parts[4] == "*"
        if len(self.minutes) > 60 // MIN_INTERVAL_MIN and self.hours == set(range(24)):
            gaps = sorted(self.minutes)
            if any(b - a < MIN_INTERVAL_MIN for a, b in zip(gaps, gaps[1:])):
                raise CronError(f"The shortest interval is {MIN_INTERVAL_MIN} minutes")

    def _day_ok(self, d: datetime) -> bool:
        if d.month not in self.months:
            return False
        dom_ok = d.day in self.days
        dow_ok = (d.isoweekday() % 7) in self.weekdays
        if self.dom_any and self.dow_any:
            return True
        if self.dom_any:
            return dow_ok
        if self.dow_any:
            return dom_ok
        return dom_ok or dow_ok  # cron semantics: either matches when both are restricted

    def next_after(self, after: datetime) -> datetime:
        """Next run strictly after `after` (aware datetime), returned in UTC."""
        if after.tzinfo is None:
            after = after.replace(tzinfo=timezone.utc)
        if self.every:
            return (after + self.every).astimezone(timezone.utc).replace(second=0, microsecond=0)
        local = after.astimezone(self.tz).replace(second=0, microsecond=0) + timedelta(minutes=1)
        day = local.replace(hour=0, minute=0)
        for _ in range(366 * 5):
            if self._day_ok(day):
                for h in sorted(self.hours):
                    for mi in sorted(self.minutes):
                        cand = day.replace(hour=h, minute=mi)
                        # normalise across DST changes
                        cand = datetime(cand.year, cand.month, cand.day, h, mi, tzinfo=self.tz)
                        if cand >= local:
                            return cand.astimezone(timezone.utc)
            day = (day + timedelta(days=1)).replace(hour=0, minute=0)
        raise CronError("This schedule never runs")

    def describe(self) -> str:
        if self.every:
            mins = int(self.every.total_seconds() // 60)
            return f"every {mins} minutes" if mins < 60 else f"every {mins // 60} hours" if mins < 1440 else f"every {mins // 1440} days"
        return f"cron '{SHORTHANDS.get(self.expr.lower(), self.expr)}' ({self.tz.key})"
