"""Read dated gas totals and observed heating intervals from Home Assistant."""
from datetime import timedelta, timezone
from functools import partial

from homeassistant.util import dt as dt_util

from .estimates import energy_kwh, reported_gas_period


def heating_intervals(states, runs, start, end):
    """Limit observations to recorded on/off states within clean recorder runs."""
    result = []
    for index, state in enumerate(states):
        if state.state not in ("on", "off"):
            continue
        left = max(start, state.last_updated.timestamp())
        right = min(end, states[index + 1].last_updated.timestamp() if index + 1 < len(states) else end)
        for run_start, run_end in runs:
            if state.last_updated.timestamp() < run_start:
                continue
            first, last = max(left, run_start), min(right, run_end)
            if first < last:
                result.append((first, last, state.state == "on"))
    return result


async def async_read_learning_history(hass, activity_id, meter_id, factor):
    from homeassistant.components.recorder import get_instance, history
    from homeassistant.components.recorder.db_schema import RecorderRuns
    from homeassistant.components.recorder.util import session_scope
    from sqlalchemy import select, or_

    end = dt_util.utcnow()
    start = end - timedelta(days=8)

    def read():
        # History needs attributes on gas entries to identify consumption dates.
        states = history.get_significant_states(
            hass, start, end, [activity_id, meter_id], significant_changes_only=False)
        with session_scope(hass=hass, read_only=True) as session:
            rows = session.execute(select(RecorderRuns).where(
                RecorderRuns.start < end, or_(RecorderRuns.end > start, RecorderRuns.end.is_(None)))).scalars()
            runs = [(row.start.replace(tzinfo=timezone.utc).timestamp(),
                     row.end.replace(tzinfo=timezone.utc).timestamp() if row.end else end.timestamp())
                    for row in rows if not row.closed_incorrect]
        intervals = heating_intervals(states.get(activity_id, []), runs, start.timestamp(), end.timestamp())
        totals = {}
        for state in states.get(meter_id, []):
            period = reported_gas_period(state.attributes.get("charges"))
            value = energy_kwh(state.state, state.attributes.get("unit_of_measurement"), factor)
            if period and value is not None and start.timestamp() <= period[0] < period[1] <= end.timestamp():
                totals[period] = value
        return intervals, totals

    return await get_instance(hass).async_add_executor_job(partial(read))
