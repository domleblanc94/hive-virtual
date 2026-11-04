# 0.4.0 validation — 4 November 2026

## Passed

- 42 offline Python tests, including shared UI/YAML validation, weekly schedules, native helper selection/gaps, persistent fault history, durable request reservation before cloud calls, lost replies, disk failures, live option updates, explicit fault reset, true-device timestamp checks, active-boost priority and notification deduplication.
- Real Home Assistant 2026.10.4 running in an isolated Docker container with networking disabled. The official Hive integration was loaded as a dependency, but no credentials were supplied. A fake Hive climate state and a simulated Hive boost service were used.
- Real-runtime config flow created an entry and loaded all three platforms: climate, binary_sensor and button. The sidebar registered, unloaded and registered again correctly.
- Runtime request handling saved its deadline before the simulated Hive action, retained Auto/Heat state on option changes, retained deadline and fault through entry unload/reload, reset a corrected fault explicitly, and accepted a native Schedule helper through the real options flow. Native helper gaps suppressed further demand.
- Browser render inspected in headless Chrome. Browser tests clicked Auto, Off and Reset fault and changed the target; the expected Home Assistant service calls were dispatched.
- JavaScript syntax checked with Node. PNG logo files inspected and packaged with transparency at 256/512 pixels.

The runtime emitted Home Assistant's standard warning that this is an unreviewed custom integration. A third-party `rich` SyntaxWarning was also emitted. The runtime test otherwise completed successfully.

Runtime image: `ghcr.io/home-assistant/home-assistant:stable`, resolved during this test to Home Assistant 2026.10.4, image digest `sha256:23f4dfbb69ce76315dbc4de7915504f1c2dcecd7ca2648113a493a385972e133`.

## Still requires the user's heating system

- Actual Hive cloud command acceptance and the physical boiler's response.
- Five-minute expiry while Home Assistant is powered off, including restoration of the low fallback target.
- Manual boosts from the real Hive app while the controller is running.
- A sustained overnight run covering area changes, sensor reporting intervals and acceptable boiler cycling/temperature overshoot.

These remain required before relying on unattended operation. The simulator does not establish physical failsafe behaviour or remove the cloud race described in README.md.

## Reproduction

From the development workspace, run the offline tests with Python and voluptuous:

```text
python -m unittest discover -s tests -p test_hive_virtual.py
```

The ZIP includes the same test under `validation/`; from an extracted ZIP use `-s validation`. The runtime script requires an isolated Home Assistant installation/container, an empty `/config` with the integration copied under `custom_components`, and no real Hive credentials. Run it only in a disposable test environment; it creates a test config entry and fake entities. For repeated runs use a fresh test configuration directory. The development browser fixture and runtime logs are under the workspace's `analysis/` folder and are not required to install the integration.
