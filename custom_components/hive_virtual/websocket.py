"""Admin-only page settings with conflict detection and shared validation."""
from copy import deepcopy
from hashlib import sha256
import json

import voluptuous as vol
from homeassistant.components import websocket_api
from .climate import validate_config


def revision(config):
    return sha256(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()


def controller_entry(hass, entity_id):
    controller = hass.data.get("hive_virtual_controllers", {}).get(entity_id)
    if controller is None:
        raise ValueError("Choose a loaded virtual thermostat")
    for entry in hass.config_entries.async_entries("hive_virtual"):
        if getattr(entry, "runtime_data", None) is controller:
            return controller, entry
    raise ValueError("YAML controllers must be edited in their configuration file")


def async_setup(hass):
    websocket_api.async_register_command(hass, get_config)
    websocket_api.async_register_command(hass, save_config)


@websocket_api.websocket_command({vol.Required("type"): "hive_virtual/config", vol.Required("entity_id"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def get_config(hass, connection, msg):
    try:
        controller, entry = controller_entry(hass, msg["entity_id"])
        config = validate_config({**entry.data, **entry.options})
        connection.send_result(msg["id"], {"revision": revision(config), "schedule": config["schedule"],
            "schedule_entity": config.get("schedule_entity"), "areas": list(config["areas"]), "cost": config["cost"]})
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_controller", str(err))


@websocket_api.websocket_command({vol.Required("type"): "hive_virtual/save", vol.Required("entity_id"): str,
    vol.Required("revision"): str, vol.Optional("schedule"): list, vol.Optional("cost"): dict})
@websocket_api.require_admin
@websocket_api.async_response
async def save_config(hass, connection, msg):
    try:
        controller, entry = controller_entry(hass, msg["entity_id"])
        config = validate_config({**entry.data, **entry.options})
        if revision(config) != msg["revision"]:
            connection.send_error(msg["id"], "conflict", "Settings changed elsewhere. Reload the editor before saving.")
            return
        updated = deepcopy(config)
        if "schedule" in msg:
            if config.get("schedule_entity"):
                raise ValueError("Edit the selected native helper instead of the inactive built-in schedule")
            updated["schedule"] = msg["schedule"]
        if "cost" in msg:
            updated["cost"] = msg["cost"]
        updated = validate_config(updated)
        hass.config_entries.async_update_entry(entry, options=updated)
        connection.send_result(msg["id"], {"saved": True})
    except (ValueError, vol.Invalid) as err:
        connection.send_error(msg["id"], "invalid_settings", str(err))
