# Move an existing manual installation to HACS

HACS manages the integration's program files. You can change how those files are installed without deleting the configured Hive Virtual Thermostat entry or recreating its temperature schedule.

## Before downloading

Create a full Home Assistant backup. Keep the existing Hive Virtual Thermostat entry under Settings → Devices & services. Keep the official Hive integration too.

Record which schedule source you currently use:

- **Built-in weekly schedule:** its periods are stored with the integration's configuration entry/options.
- **Native Schedule helper:** its blocks belong to the separate helper; the thermostat stores the helper entity reference.
- **YAML configuration:** the thermostat and schedule remain in configuration.yaml or your included configuration files. HACS adoption does not require converting YAML to a UI entry.

Your configured sensors, entity IDs, integration options and fault/request state are separate from the downloaded custom_components/hive_virtual code. Do not delete Home Assistant storage files or remove/recreate the integration as part of this migration.

## Adopt the installation in HACS

1. Open HACS → menu (⋮) → Custom repositories.
2. Add https://github.com/domleblanc94/hive-virtual with type Integration.
3. Find Hive Virtual Thermostat and download its published release. HACS needs to perform this download once before it can track a previously manual installation.
4. Restart Home Assistant fully, then refresh the browser.
5. Open the existing integration's Configure options. Check the sensor selections, schedule source and built-in periods or native helper reference.
6. Select Auto when ready to resume scheduled heating. Full Home Assistant restarts start the thermostat Off by design.

Do not add another virtual thermostat for the same Hive zone. If you already use YAML, retain that configuration block. The initial migration changes file management, not your chosen configuration method.

## Verify the migration

- HACS shows Hive Virtual Thermostat as downloaded with a published version.
- Your original virtual climate entity remains available under the same entity ID.
- Schedule periods or native helper blocks still contain your existing temperatures and times.
- Auto selects the expected temperature and upstairs/downstairs area for the current local time.
- Existing dashboards still reference the same climate entity. If an older manual dashboard and the automatic sidebar both use the title Hive Heating, remove only the redundant dashboard if desired.

For an unexpected error or missing configuration, keep the backup and investigate before recreating entries. Recreating a config entry can lose its saved options and create duplicate entities.

## Future updates

Install new published versions through HACS and restart Home Assistant. Check the release notes, confirm settings and select Auto/Heat again when ready. A version update is not a physical heating test: supervised verification of Hive boost expiry and fallback behaviour is still required as described in README.md.

References: [HACS existing installations](https://www.hacs.xyz/docs/faq/existing_elements/), [custom repositories](https://www.hacs.xyz/docs/faq/custom_repositories/), and the integration's settings/persistence implementation in custom_components/hive_virtual.
