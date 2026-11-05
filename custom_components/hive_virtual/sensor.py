"""Recorder-compatible floor averages, heating runtime and cost estimates."""
from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass


async def async_setup_entry(hass, entry, async_add_entities):
    controller = entry.runtime_data
    entities = [HeatingSensor(controller, "area_temperature", area) for area in controller.config["areas"]]
    entities += [HeatingSensor(controller, kind) for kind in (
        "target_temperature", "heating_hours", "heating_kwh", "heating_cost", "household_gas_kwh", "household_variable_cost", "sensor_fallback")]
    async_add_entities(entities)


class HeatingSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, controller, kind, area=None):
        self.controller, self.kind, self.area = controller, kind, area
        self._attr_unique_id = controller._attr_unique_id + "_" + kind + ("_" + area if area else "")
        labels = {"target_temperature": "Heating target", "heating_hours": "Heating hours today", "heating_kwh": "Estimated heating gas today",
                  "heating_cost": "Estimated heating cost today", "household_gas_kwh": "Household gas today",
                  "household_variable_cost": "Household gas cost today", "sensor_fallback": "Sensor fallback"}
        self._attr_name = area + " temperature" if area else labels[kind]
        if kind in ("area_temperature", "target_temperature"):
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = "°C"
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif kind == "sensor_fallback":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_native_unit_of_measurement = None
            self._attr_options = ["inactive", "boost_active", "hive_settings", "fixed_target", "unconfirmed"]
        elif kind.endswith("kwh"):
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = "kWh"
        elif kind.endswith("cost"):
            self._attr_device_class = SensorDeviceClass.MONETARY
        else:
            self._attr_native_unit_of_measurement = "h"
            self._attr_device_class = SensorDeviceClass.DURATION

    @property
    def device_info(self):
        return self.controller.device_info

    @property
    def native_unit_of_measurement(self):
        return self.controller.config["cost"]["currency"] if self.kind.endswith("cost") else self._attr_native_unit_of_measurement

    @property
    def native_value(self):
        if self.area:
            return self.controller.area_temperature(self.area)
        if self.kind == "target_temperature":
            return self.controller.target_temperature
        if self.kind == "sensor_fallback":
            return self.controller.sensor_fallback_status or "inactive"
        if self.kind == "heating_hours":
            return round(self.controller._runtime.seconds / 3600, 4)
        value = self.controller.cost_estimates().get(self.kind)
        return round(value, 4) if value is not None else None

    @property
    def extra_state_attributes(self):
        return {"controller_unique_id": self.controller._attr_unique_id, "heating_role": self.kind,
                "area": self.area, "period_start": self.controller._runtime.day,
                "basis": "Runtime × configured gas input power × current tariff" if self.kind == "heating_cost" else
                "Whole-house gas; may include hot water and cooking" if self.kind.startswith("household") else
                "Reported heating intervals; unavailable gaps are omitted" if self.kind == "heating_hours" else None}

    async def async_added_to_hass(self):
        self.async_on_remove(self.controller.subscribe(self.async_write_ha_state))
