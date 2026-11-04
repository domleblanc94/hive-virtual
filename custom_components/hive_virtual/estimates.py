"""Explicit, reproducible estimates; no gas-volume or tariff-unit guessing."""
from math import isfinite


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
