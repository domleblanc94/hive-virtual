"""Visible problem state for automations and dashboards."""
from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.util import dt as dt_util


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([HiveFault(entry.runtime_data), HiveHeatingActivity(entry.runtime_data)])


class HiveFault(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Heating fault"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_should_poll = False

    def __init__(self, controller):
        self.controller = controller
        self._attr_unique_id = controller._attr_unique_id + "_fault"

    @property
    def device_info(self):
        return self.controller.device_info

    @property
    def is_on(self):
        return bool(self.controller.fault_message)

    @property
    def extra_state_attributes(self):
        return {"reason": self.controller.fault_message,
                "fault_latched": bool(self.controller._latched_fault)}

    async def async_added_to_hass(self):
        self.async_on_remove(self.controller.subscribe(self.async_write_ha_state))


class HiveHeatingActivity(HiveFault):
    _attr_name = "Reported heating"
    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, controller):
        super().__init__(controller)
        self._attr_unique_id = controller._attr_unique_id + "_reported_heating"

    @property
    def available(self):
        state = self.controller.hass.states.get(self.controller.config["hive_entity"])
        return bool(state and state.state not in ("unknown", "unavailable") and
                    0 <= (dt_util.utcnow() - state.last_reported).total_seconds() <= 120 and
                    state.attributes.get("hvac_action") in ("heating", "idle", "off"))

    @property
    def is_on(self):
        state = self.controller.hass.states.get(self.controller.config["hive_entity"])
        return bool(state and state.attributes.get("hvac_action") == "heating")

    @property
    def extra_state_attributes(self):
        return {"controller_unique_id": self.controller._attr_unique_id, "heating_role": "activity"}
