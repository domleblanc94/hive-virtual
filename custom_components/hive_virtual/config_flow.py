"""Home Assistant forms for room selection, safety settings and weekly schedules."""
from copy import deepcopy

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import DOMAIN
from .climate import validate_config

DEFAULT_SCHEDULE = [
    {"time": "06:30", "temperature": 20, "area": "Downstairs", "days": list(range(7))},
    {"time": "08:30", "temperature": 17, "area": "Downstairs", "days": list(range(7))},
    {"time": "17:00", "temperature": 20.5, "area": "Downstairs", "days": list(range(7))},
    {"time": "22:00", "temperature": 18, "area": "Upstairs", "days": list(range(7))},
]


def sensors_schema(config, include_hive=False):
    fields = {}
    if include_hive:
        fields[vol.Required("name", default=config.get("name", "House virtual heating"))] = str
        fields[vol.Required("hive_entity", default=config.get("hive_entity", vol.UNDEFINED))] = selector.EntitySelector({"filter": {"domain": "climate", "integration": "hive"}})
    for area, key in (("Downstairs", "downstairs_sensors"), ("Upstairs", "upstairs_sensors")):
        fields[vol.Required(key, default=config.get("areas", {}).get(area, []))] = selector.EntitySelector({
            "filter": {"domain": "sensor", "device_class": "temperature"}, "multiple": True})
    fields[vol.Required("default_area", default=config.get("default_area", "Downstairs"))] = selector.SelectSelector({"options": ["Downstairs", "Upstairs"]})
    fields[vol.Optional("sensor_report_entities", default=config.get("sensor_report_entities", {}))] = selector.ObjectSelector()
    return vol.Schema(fields)


def apply_sensors(config, user_input):
    config = deepcopy(config)
    for key in ("downstairs_sensors", "upstairs_sensors"):
        if not user_input[key]:
            raise vol.Invalid("missing_sensors", path=[key])
    config["areas"] = {"Downstairs": user_input["downstairs_sensors"], "Upstairs": user_input["upstairs_sensors"]}
    config["default_area"] = user_input["default_area"]
    if "sensor_report_entities" in user_input:
        config["sensor_report_entities"] = user_input["sensor_report_entities"]
    return config


def validation_errors(error):
    """Attach actionable errors to fields without hiding schema failures."""
    first = error.errors[0] if isinstance(error, vol.MultipleInvalid) else error
    code = str(getattr(first, "msg", ""))
    if code in {"missing_sensors", "fallback_not_lower", "sensor_limits_reversed"}:
        return {first.path[0]: code}
    if getattr(first, "path", None) and first.path[0] not in ("areas", "schedule"):
        return {first.path[0]: "invalid_value"}
    return {"base": "invalid_settings"}


class HiveVirtualConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input["hive_entity"])
            self._abort_if_unique_id_configured()
            # Prevent a YAML controller and a UI controller from competing.
            if any(state.attributes.get("hive_entity") == user_input["hive_entity"] for state in self.hass.states.async_all("climate")):
                return self.async_abort(reason="controller_exists")
            try:
                config = apply_sensors({"name": user_input["name"], "hive_entity": user_input["hive_entity"],
                    "unique_id": user_input["hive_entity"] + "_virtual", "call_temperature": 25,
                    "sensor_timeout": 30, "schedule": deepcopy(DEFAULT_SCHEDULE)}, user_input)
                config = validate_config(config)
            except (vol.Invalid, ValueError) as err:
                errors = validation_errors(err)
            else:
                return self.async_create_entry(title=config["name"], data=config)
        shown = {}
        if user_input:
            shown = {"name": user_input.get("name", "House virtual heating"),
                     "hive_entity": user_input.get("hive_entity"),
                     "areas": {"Downstairs": user_input.get("downstairs_sensors", []), "Upstairs": user_input.get("upstairs_sensors", [])},
                     "default_area": user_input.get("default_area", "Downstairs")}
        return self.async_show_form(step_id="user", data_schema=sensors_schema(shown, True), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return HiveVirtualOptionsFlow()


class HiveVirtualOptionsFlow(config_entries.OptionsFlow):
    """Save validated settings and update the running controller in place."""

    def _config(self):
        return deepcopy({**self.config_entry.data, **self.config_entry.options})

    def _save(self, config):
        return self.async_create_entry(title="", data=validate_config(config))

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(step_id="init", menu_options=["sensors", "schedule_source", "schedule", "safety"])

    async def async_step_schedule_source(self, user_input=None):
        config = self._config()
        errors = {}
        if user_input is not None:
            if user_input["use_schedule_helper"] and not user_input.get("schedule_entity"):
                errors["schedule_entity"] = "missing_schedule"
            else:
                if user_input["use_schedule_helper"]:
                    config["schedule_entity"] = user_input["schedule_entity"]
                else:
                    config.pop("schedule_entity", None)
                return self._save(config)
        fields = {vol.Required("use_schedule_helper", default=bool(config.get("schedule_entity"))): selector.BooleanSelector()}
        key = vol.Optional("schedule_entity", default=config["schedule_entity"]) if config.get("schedule_entity") else vol.Optional("schedule_entity")
        fields[key] = selector.EntitySelector({"filter": {"domain": "schedule"}})
        return self.async_show_form(step_id="schedule_source", errors=errors, data_schema=vol.Schema(fields))

    async def async_step_sensors(self, user_input=None):
        config = self._config()
        errors = {}
        if user_input is not None:
            try:
                return self._save(apply_sensors(config, user_input))
            except (vol.Invalid, ValueError) as err:
                errors = validation_errors(err)
                config = {**config, "areas": {"Downstairs": user_input.get("downstairs_sensors", []),
                          "Upstairs": user_input.get("upstairs_sensors", [])},
                          "default_area": user_input.get("default_area", "Downstairs")}
        return self.async_show_form(step_id="sensors", data_schema=sensors_schema(config), errors=errors)

    async def async_step_safety(self, user_input=None):
        config = self._config()
        errors = {}
        if user_input is not None:
            try:
                return self._save({**config, **user_input})
            except (vol.Invalid, ValueError) as err:
                errors = validation_errors(err)
                config = {**config, **user_input}
        limits = {
            "call_temperature": (7, 32, 0.5), "idle_temperature": (7, 30, 0.5),
            "boost_minutes": (1, 15, 1), "tolerance": (0.1, 2, 0.1),
            "sensor_timeout": (1, 240, 1), "sensor_min": (-10, 30, 0.5),
            "sensor_max": (10, 50, 0.5), "max_sensor_jump": (0.1, 10, 0.1),
            "jump_window": (1, 120, 1), "max_sensor_spread": (0.5, 20, 0.5),
            "high_temperature_cutoff": (10, 35, 0.5),
            "cooldown_minutes": (1, 60, 1),
        }
        fields = {vol.Required(key, default=config.get(key, 2)): selector.NumberSelector({
            "min": low, "max": high, "step": step, "mode": "box"})
            for key, (low, high, step) in limits.items()}
        fields[vol.Required("notifications", default=config.get("notifications", True))] = selector.BooleanSelector()
        fields[vol.Required("use_sensor_availability", default=config.get("use_sensor_availability", False))] = selector.BooleanSelector()
        fields[vol.Required("allow_partial_area", default=config.get("allow_partial_area", False))] = selector.BooleanSelector()
        fields[vol.Required("preserve_hive_settings", default=config.get("preserve_hive_settings", False))] = selector.BooleanSelector()
        schema = vol.Schema(fields)
        return self.async_show_form(step_id="safety", data_schema=schema, errors=errors)

    async def async_step_schedule(self, user_input=None):
        return self.async_show_menu(step_id="schedule", menu_options=["add_period", "edit_period", "delete_period"])

    async def async_step_add_period(self, user_input=None):
        return await self._period_form("add_period", user_input)

    def _period_choices(self):
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        return [{"value": str(index), "label": f"{slot['time']} · {slot['temperature']}°C · {slot['area']} · " +
                 ", ".join(days[d] for d in slot["days"])}
                for index, slot in enumerate(self._config()["schedule"])]

    async def async_step_edit_period(self, user_input=None):
        if user_input is not None:
            self._editing = int(user_input["period"])
            return await self.async_step_period()
        choices = self._period_choices()
        if not choices:
            return self.async_abort(reason="empty_schedule")
        return self.async_show_form(step_id="edit_period", data_schema=vol.Schema({
            vol.Required("period"): selector.SelectSelector({"options": choices})}))

    async def async_step_period(self, user_input=None):
        return await self._period_form("period", user_input, self._editing)

    async def _period_form(self, step, user_input, index=None):
        config = self._config()
        old = config["schedule"][index] if index is not None else {"time": "12:00", "temperature": 20, "area": "Downstairs", "days": list(range(7))}
        errors = {}
        if user_input is not None:
            try:
                period = {"time": user_input["time"][:5], "temperature": user_input["temperature"],
                          "area": user_input["area"], "days": [int(day) for day in user_input["days"]]}
                if index is None:
                    config["schedule"].append(period)
                else:
                    config["schedule"][index] = period
                return self._save(config)
            except (vol.Invalid, ValueError):
                errors["base"] = "invalid_schedule"
        return self.async_show_form(step_id=step, errors=errors, data_schema=vol.Schema({
            vol.Required("time", default=old["time"] + ":00"): selector.TimeSelector(),
            vol.Required("temperature", default=old["temperature"]): selector.NumberSelector({"min": 7, "max": 30, "step": 0.5, "mode": "box"}),
            vol.Required("area", default=old["area"]): selector.SelectSelector({"options": list(config["areas"])}),
            vol.Required("days", default=[str(d) for d in old["days"]]): selector.SelectSelector({"multiple": True, "options": [
                {"value": str(i), "label": day} for i, day in enumerate(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])]}),
        }))

    async def async_step_delete_period(self, user_input=None):
        config = self._config()
        if user_input is not None:
            config["schedule"].pop(int(user_input["period"]))
            return self._save(config)
        choices = self._period_choices()
        if not choices:
            return self.async_abort(reason="empty_schedule")
        return self.async_show_form(step_id="delete_period", data_schema=vol.Schema({
            vol.Required("period"): selector.SelectSelector({"options": choices})}))
