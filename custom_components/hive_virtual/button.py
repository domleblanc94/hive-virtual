"""Explicit acknowledgement of checked sensor faults."""
from homeassistant.components.button import ButtonEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ResetFault(entry.runtime_data)])


class ResetFault(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Reset heating fault"
    _attr_icon = "mdi:alert-circle-check"

    def __init__(self, controller):
        self.controller = controller
        self._attr_unique_id = controller._attr_unique_id + "_reset_fault"

    @property
    def device_info(self):
        return self.controller.device_info

    @property
    def extra_state_attributes(self):
        return {"thermostat_unique_id": self.controller._attr_unique_id}

    async def async_press(self):
        await self.controller.async_reset_fault()
