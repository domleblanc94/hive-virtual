"""Explicit, reproducible estimates; no gas-volume or tariff-unit guessing."""
from math import isfinite
from statistics import median
from datetime import datetime


def number(value):
    try:
        result = float(value)
        return result if isfinite(result) and result >= 0 else None
    except (TypeError, ValueError):
        return None


def energy_kwh(value, unit, volume_factor=0):
    value = number(value)
    if value is None:
        return None
    if unit == "kWh":
        return value
    if unit == "Wh":
        return value / 1000
    if unit == "MWh":
        return value * 1000
    if unit in ("m³", "m3") and volume_factor > 0:
        return value * volume_factor
    return None


def price(value, minor_units=False):
    value = number(value)
    return value / 100 if value is not None and minor_units else value


def estimate(hours, input_kw, rate, measured_energy=None, measured_cost=None, standing=None):
    """Measured gas is household-wide; heating-only energy uses input-power estimate."""
    heat_energy = hours * input_kw if input_kw > 0 else None
    return {
        "heating_kwh": heat_energy,
        "heating_cost": heat_energy * rate if heat_energy is not None and rate is not None else None,
        "household_gas_kwh": measured_energy,
        "household_variable_cost": measured_cost if measured_cost is not None else (
            measured_energy * rate if measured_energy is not None and rate is not None else None),
        "standing_charge": standing,
    }


class DailyRuntime:
    """Integrate reported heating, omitting unknown intervals and long gaps."""

    def __init__(self, saved=None):
        saved = saved or {}
        self.day = saved.get("day")
        self.seconds = number(saved.get("seconds")) or 0
        self.last = None
        self.on = None

    def update(self, timestamp, day, seconds_since_midnight, on):
        elapsed = timestamp - self.last if self.last is not None else 0
        changed_day = day != self.day
        if changed_day:
            self.day, self.seconds = day, 0
        if self.on is True and on is not None and 0 < elapsed <= 120:
            self.seconds += min(elapsed, seconds_since_midnight) if changed_day else elapsed
        self.last, self.on = timestamp, on

    def saved(self):
        return {"day": self.day, "seconds": self.seconds}


class GasLearning:
    """Estimate input power from meter readings inside observed heating runs."""

    def __init__(self, source=None, saved=None):
        self.source = source
        self.runs = []
        if source and isinstance(saved, dict) and saved.get("source") == list(source):
            runs = saved.get("runs")
            if isinstance(runs, list):
                self.runs = [kw for value in runs[-12:] if (kw := number(value)) and kw <= 100]
        self.last = None
        self.on = None
        self.started = None
        self.first = self.latest = None

    @property
    def kw(self):
        return median(self.runs) if len(self.runs) >= 3 else None

    def update(self, now, on, reading):
        continuous = self.last is not None and 0 <= now - self.last <= 120
        if not continuous or on is None:
            self.started = None
        elif on and self.on is False:
            self.started = now
            self.first = self.latest = None
        self.last = now
        self.on = on
        if self.started is None:
            return
        if on is False:
            self.started = None
            if self.first and self.latest:
                elapsed = self.latest[0] - self.first[0]
                if elapsed >= 180:
                    power = (self.latest[1] - self.first[1]) * 3600 / elapsed
                    if 0 < power <= 100:
                        self.runs = (self.runs + [power])[-12:]
            return
        if reading is None:
            self.started = None
            return
        reported, energy, unit = reading
        if not self.started < reported <= now:
            return
        if self.latest and (reported < self.latest[0] or energy < self.latest[1] or unit != self.latest[2]):
            self.started = None
            return
        self.first = self.first or reading
        self.latest = reading

    def saved(self):
        return {"source": list(self.source), "runs": self.runs} if self.source else None


def reported_gas_period(charges):
    """Read the exact consumption window from a complete set of half-hour rows."""
    if not isinstance(charges, (list, tuple)) or not 46 <= len(charges) <= 50:
        return None
    try:
        periods = []
        for row in charges:
            bounds = tuple(row[key] for key in ("start", "end"))
            bounds = [value if isinstance(value, datetime) else datetime.fromisoformat(value) for value in bounds]
            if any(value.utcoffset() is None for value in bounds):
                return None
            periods.append(tuple(value.timestamp() for value in bounds))
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    periods.sort()
    start = periods[0][0]
    if start % 1800 or any(pair != (start + index * 1800, start + (index + 1) * 1800)
                           for index, pair in enumerate(periods)):
        return None
    return int(start), int(periods[-1][1])


class DelayedGasLearning:
    """Match supplier windows to observed heating, keyed by time of consumption."""

    def __init__(self, source=None, saved=None):
        saved = saved if isinstance(saved, dict) and source and saved.get("source") == list(source) else {}
        self.source = source
        self.buckets = saved.get("buckets", {})
        self.days = saved.get("days", {})
        self.last = None
        self.on = None

    def update(self, now, on):
        cutoff = int(now // 1800) - 48 * 8
        if self.buckets and int(next(iter(self.buckets))) < cutoff:
            self.buckets = {key: value for key, value in self.buckets.items() if int(key) >= cutoff}
        if self.on is not None and on is not None and 0 < now - self.last <= 120:
            start = self.last
            while start < now:
                bucket = int(start // 1800)
                end = min(now, (bucket + 1) * 1800)
                covered, heated = self.buckets.get(str(bucket), (0, 0))
                self.buckets[str(bucket)] = [covered + end - start, heated + (end - start if self.on else 0)]
                start = end
        self.last, self.on = now, on

    def match(self, start, end, kwh, now):
        cutoff = int(now // 1800) - 48 * 8
        if end > now or start // 1800 < cutoff:
            return
        observations = [self.buckets.get(str(bucket), (0, 0)) for bucket in range(start // 1800, end // 1800)]
        covered = sum(value[0] for value in observations)
        heated = sum(value[1] for value in observations)
        if covered < .95 * (end - start) or heated < 3600:
            return
        key = str(start)
        kw = kwh * 3600 / heated
        if 0 < kw <= 100:
            self.days[key] = kw
        else:
            self.days.pop(key, None)
        self.days = dict(sorted(self.days.items(), key=lambda item: int(item[0]))[-12:])

    @property
    def kw(self):
        return median(self.days.values()) if len(self.days) >= 3 else None

    def saved(self):
        return {"source": list(self.source), "buckets": self.buckets.copy(), "days": self.days.copy()} if self.source else None
