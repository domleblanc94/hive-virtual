"""Virtual room-temperature control layered over the official Hive integration."""

from homeassistant.helpers import config_validation as cv

DOMAIN = "hive_virtual"
PLATFORMS = ["climate", "binary_sensor", "button", "sensor"]
CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


async def async_setup(hass, config):
    """Provide explicit fault acknowledgement for YAML and UI controllers."""
    import voluptuous as vol
    from homeassistant.helpers import config_validation as cv

    async def reset_fault(call):
        entity_id = call.data["entity_id"]
        controller = hass.data.get("hive_virtual_controllers", {}).get(entity_id)
        if controller is None:
            raise ValueError("Select a loaded Hive virtual thermostat")
        await controller.async_reset_fault()

    hass.services.async_register(DOMAIN, "reset_fault", reset_fault,
                                schema=vol.Schema({vol.Required("entity_id"): cv.entity_id}))
    from .websocket import async_setup as async_setup_websocket
    async_setup_websocket(hass)
    return True


async def async_setup_entry(hass, entry):
    """Share one controller across entities and register the sidebar once."""
    from .climate import HiveVirtual, validate_config
    from .panel import async_setup_panel
    entry.runtime_data = HiveVirtual(validate_config({**entry.data, **entry.options}))
    await async_setup_panel(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    hass.data.setdefault("hive_virtual_entries", set()).add(entry.entry_id)
    return True


async def _async_options_updated(hass, entry):
    await entry.runtime_data.async_update_config({**entry.data, **entry.options})


async def async_unload_entry(hass, entry):
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        from .panel import async_remove_panel
        entries = hass.data.setdefault("hive_virtual_entries", set())
        entries.discard(entry.entry_id)
        if not entries:
            async_remove_panel(hass)
    return unloaded
