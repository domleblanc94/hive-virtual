"""Pure control decisions, independent of Home Assistant."""
from math import isfinite


def schedule_slot(entries, now):
    """Most recent weekly entry in local wall time, including last week's wrap."""
    if not entries:
        return None
    minute = now.weekday() * 1440 + now.hour * 60 + now.minute
    candidates = []
    for entry in entries:
        hour, minute_part = map(int, entry["time"].split(":"))
        for day in entry.get("days", range(7)):
            start = day * 1440 + hour * 60 + minute_part
            candidates.append(((minute - start) % 10080, entry))
    return min(candidates, key=lambda pair: pair[0])[1]


class SensorGuard:
    """Reject outliers before averaging; keep the last accepted reference."""

    def __init__(self):
        self.accepted = {}

    def check(self, readings, now, minimum, maximum, max_jump, jump_window, max_spread):
        for entity, value in readings.items():
            if not isfinite(value) or not minimum <= value <= maximum:
                return f"out_of_range:{entity}"
            if entity in self.accepted:
                previous, when = self.accepted[entity]
                if (now - when).total_seconds() <= jump_window * 60 and abs(value - previous) > max_jump:
                    return f"sudden_jump:{entity}"
        if max(readings.values()) - min(readings.values()) > max_spread:
            return "sensor_disagreement"
        for entity, value in readings.items():
            self.accepted[entity] = (value, now)
        return None


def average(values):
    """Require all configured sensors; never silently change the room weighting."""
    try:
        numbers = [float(value) for value in values]
    except (ValueError, TypeError):
        return None
    if not numbers or not all(isfinite(value) for value in numbers):
        return None
    return sum(numbers) / len(numbers)


def demand(enabled, temperature, target, previous, tolerance):
    """Use a symmetric deadband to avoid rapidly cycling the boiler."""
    if not enabled or temperature is None:
        return False
    if temperature <= target - tolerance:
        return True
    if temperature >= target + tolerance:
        return False
    return previous
