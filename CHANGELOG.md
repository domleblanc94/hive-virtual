# Changelog

## 0.6.3

- Show which active-floor sensors contribute to the average, the minimum required, and whether Hive fallback is active.
- Set different minimum available sensor counts upstairs and downstairs when partial-area control is enabled.
- Show Home Assistant notifications for single-sensor operation, fallback and recovery, and record fallback transitions for the History tab.

## 0.6.2

- Optionally keep using the remaining temperature sensors on the active floor when some are unavailable. Stop new boosts when none remain.

## 0.6.1

- Add an optional availability check for temperature sensors that only report changes. Missing, unknown and unavailable readings still block new virtual boosts.

## 0.6.0

- Keep Hive’s own mode and target between timed boosts, or configure a fixed fallback up to 30°C within the device and boost limits.
- Keep the thermostat visible across Schedule, History, and Gas & costs views.
- Clarify floor switching in the schedule editor and add search to gas sensor pickers.

## 0.5.0

- Edit built-in periods and native Schedule helper blocks directly on Hive Heating.
- Administrator-only settings commands with integration revision checks; preserve unrelated native block data.
- Recorded heating on/off/unavailable timeline, upstairs/downstairs averages and target history.
- Daily reported heating runtime, daily household gas/cost sensor inputs and runtime-based heating cost estimates.
- Explicit energy/volume and pence/currency settings, optional standing-charge display, no automatic double counting.
- New monitoring sensors; stale Hive reports do not count as observed heating.
- Retain existing schedules, mode during live edits, safety latches and request deadlines.
- Expanded Python, JavaScript, browser and real Home Assistant 2026.10.4 runtime checks.

Graphs require Recorder/History and begin collecting with the new sensors. Costs are estimates, not billing data. A full Home Assistant restart still starts Off. Physical Hive acceptance tests remain required.

## 0.4.0

First public HACS release; experimental heating control.

- Upstairs/downstairs temperature averages and timed Hive requests over a low fallback.
- Reported active Hive boosts are not renewed or cancelled.
- Native Schedule helper support and optional built-in weekly periods.
- Automatic Hive Heating sidebar and local icon/logo files.
- Persistent fault latches and request deadlines saved before cloud commands.
- Problem sensor, explicit reset button/action and persistent notifications.
- Live settings, configurable cooldown and optional actual-device report timestamps.
- 42 offline tests and an isolated Home Assistant 2026.10.4 runtime smoke test.

Physical Hive/boiler tests remain outstanding. Test expiry with Home Assistant powered off before relying on unattended use. Cloud timing races remain possible. Full Home Assistant restarts start the virtual thermostat Off.
