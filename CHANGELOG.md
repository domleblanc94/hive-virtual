# Changelog

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
