"""Scheduled room control using bounded Hive boosts over a low fallback."""
import asyncio
from datetime import timedelta
import logging
from hashlib import sha256

import voluptuous as vol

from homeassistant.components.climate import (
    PLATFORM_SCHEMA, ClimateEntity, ClimateEntityFeature, HVACAction, HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from .control import SensorGuard, average, demand, schedule_slot

_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = vol.Schema({
    vol.Required("name"): cv.string,
    vol.Required("hive_entity"): cv.entity_id,
    vol.Required("areas"): {cv.string: vol.All(cv.ensure_list, [cv.entity_id], vol.Length(min=1))},
    vol.Optional("active_area_entity"): cv.entity_id,
    vol.Optional("default_area"): cv.string,
    vol.Optional("unique_id"): cv.string,
    vol.Optional("schedule_entity"): cv.entity_id,
    vol.Optional("sensor_report_entities", default={}): {cv.entity_id: cv.entity_id},
    vol.Optional("notifications", default=True): cv.boolean,
    vol.Optional("cooldown_minutes", default=2): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
    vol.Optional("target_temperature", default=20): vol.All(vol.Coerce(float), vol.Range(min=7, max=30)),
    vol.Optional("call_temperature", default=32): vol.All(vol.Coerce(float), vol.Range(min=7, max=32)),
    vol.Optional("idle_temperature", default=7): vol.All(vol.Coerce(float), vol.Range(min=7, max=10)),
    vol.Optional("tolerance", default=0.3): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=2)),
    vol.Optional("sensor_timeout", default=120): vol.All(vol.Coerce(int), vol.Range(min=1)),
    vol.Optional("boost_minutes", default=5): vol.All(vol.Coerce(int), vol.Range(min=1, max=15)),
    vol.Optional("sensor_min", default=5): vol.All(vol.Coerce(float), vol.Range(min=-10, max=30)),
    vol.Optional("sensor_max", default=35): vol.All(vol.Coerce(float), vol.Range(min=10, max=50)),
    vol.Optional("max_sensor_jump", default=3): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=10)),
    vol.Optional("jump_window", default=10): vol.All(vol.Coerce(int), vol.Range(min=1, max=120)),
    vol.Optional("max_sensor_spread", default=6): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=20)),
    vol.Optional("high_temperature_cutoff", default=28): vol.All(vol.Coerce(float), vol.Range(min=10, max=35)),
    vol.Optional("schedule", default=[]): [vol.Schema({
        vol.Required("time"): vol.Match(r"^(?:[01]\d|2[0-3]):[0-5]\d$"),
        vol.Required("temperature"): vol.All(vol.Coerce(float), vol.Range(min=7, max=30)),
        vol.Required("area"): cv.string,
        vol.Optional("days", default=list(range(7))): vol.All([vol.All(int, vol.Range(min=0, max=6))], vol.Length(min=1)),
    })],
})
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(CONFIG_SCHEMA.schema)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Add an independent virtual climate entity."""
    async_add_entities([HiveVirtual(validate_config(config))])


async def async_setup_entry(hass, entry, async_add_entities):
    """Add the same controller through a UI config entry."""
    async_add_entities([entry.runtime_data])


def validate_config(config):
    """Share numeric, schedule and relationship checks between YAML and UI."""
    # UI entries do not carry YAML's required `platform` key. Validate engine
    # settings independently; the platform loader validates YAML metadata.
    config = CONFIG_SCHEMA({key: value for key, value in config.items()
                            if key not in ("platform", "scan_interval", "entity_namespace")})
    if not config["areas"]:
        raise vol.Invalid("At least one temperature area is required")
    if config.get("default_area", next(iter(config["areas"]))) not in config["areas"]:
        raise vol.Invalid("default_area must name a configured area")
    if config["idle_temperature"] >= config["call_temperature"]:
        raise vol.Invalid("fallback_not_lower", path=["idle_temperature"])
    if not config["hive_entity"].startswith("climate."):
        raise vol.Invalid("hive_entity must be a climate entity")
    if config["sensor_min"] >= config["sensor_max"]:
        raise vol.Invalid("sensor_limits_reversed", path=["sensor_min"])
    if config.get("schedule_entity") and not config["schedule_entity"].startswith("schedule."):
        raise vol.Invalid("Select a Schedule helper", path=["schedule_entity"])
    seen = set()
    for slot in config["schedule"]:
        if slot["area"] not in config["areas"]:
            raise vol.Invalid("Every schedule area must name a configured area")
        for day in slot["days"]:
            key = day, slot["time"]
            if key in seen:
                raise vol.Invalid("Schedule has overlapping start times")
            seen.add(key)
    return config


class HiveVirtual(ClimateEntity, RestoreEntity):
    """Use bounded boosts over a confirmed low baseline; never cancel boosts."""

    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.AUTO, HVACMode.HEAT, HVACMode.OFF]
    _attr_min_temp = 7
    _attr_max_temp = 30
    _attr_target_temperature_step = 0.5
    _attr_supported_features = (ClimateEntityFeature.TARGET_TEMPERATURE
                                | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF)

    def __init__(self, config):
        self.config = {"boost_minutes": 5, "sensor_min": 5, "sensor_max": 35,
                       "max_sensor_jump": 3, "jump_window": 10, "max_sensor_spread": 6,
                       "high_temperature_cutoff": 28, "schedule": [], **config}
        self._attr_name = config["name"]
        self._attr_unique_id = config.get("unique_id", config["hive_entity"] + "_virtual")
        self._attr_target_temperature = config["target_temperature"]
        self._attr_hvac_mode = HVACMode.OFF
        self._demand = False
        self._lock = asyncio.Lock()
        self._status = "starting"
        self._area = None
        self._attr_current_temperature = None
        self._guard = SensorGuard()
        self._fault = None
        self._latched_fault = None
        self._pending_until = None
        self._started = dt_util.utcnow()
        self._slot = None
        self._store = None
        self._storage_fault = False
        self._saved = None
        self._last_saved_at = None
        self._listeners = set()
        self._unwatch = None
        self._removed = False
        self._notice = None
        self._tasks = set()
        self._was_boosting = False
        self.config.setdefault("notifications", True)
        self.config.setdefault("cooldown_minutes", 2)
        self.config.setdefault("sensor_report_entities", {})

    @property
    def device_info(self):
        return {"identifiers": {("hive_virtual", self._attr_unique_id)}, "name": self.name,
                "manufacturer": "Community integration", "model": "Hive Virtual Thermostat"}

    def subscribe(self, listener):
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    @property
    def fault_message(self):
        if self._storage_fault:
            return "Safety state could not be loaded or saved"
        return self._latched_fault or self._fault or (self._status if self._status in (
            "control_error", "hive_unavailable", "unsupported_hive_entity", "unsupported_temperature_unit",
            "target_out_of_range", "unknown_boost_state") else None)

    def _safety_data(self):
        return {"fault": self._latched_fault,
                "pending_until": self._pending_until.isoformat() if self._pending_until else None,
                "accepted": {key: [value, when.isoformat()] for key, (value, when) in self._guard.accepted.items()}}

    async def _persist(self):
        if self._storage_fault:
            return False
        if self._store is None:
            return True  # Before entity setup; production commands begin after loading.
        data = self._safety_data()
        if data == self._saved:
            return True
        # Faults and request reservations are immediate. Sample unchanged
        # temperature history at most every five minutes to limit disk writes.
        if self._saved and self._last_saved_at and (dt_util.utcnow() - self._last_saved_at).total_seconds() < 300:
            if data["fault"] == self._saved["fault"] and data["pending_until"] == self._saved["pending_until"]:
                return True
        try:
            await self._store.async_save(data)
        except Exception:
            self._storage_fault = True
            _LOGGER.exception("Could not save virtual thermostat safety state")
            return False
        self._saved = data
        self._last_saved_at = dt_util.utcnow()
        return True

    def _watch(self):
        if self._unwatch:
            self._unwatch()
        watched = {self.config["hive_entity"]}
        for sensors in self.config["areas"].values():
            watched.update(sensors)
        for key in ("active_area_entity", "schedule_entity"):
            if entity := self.config.get(key):
                watched.add(entity)
        watched.update(self.config["sensor_report_entities"].values())
        self._unwatch = async_track_state_change_event(self.hass, watched, self._changed)

    async def async_update_config(self, config):
        async with self._lock:
            self.config = validate_config(config)
            self._watch()
        await self._reconcile()

    async def async_reset_fault(self):
        if self.hvac_mode != HVACMode.OFF:
            raise ValueError("Select Off and check the sensors before resetting the fault")
        async with self._lock:
            old_fault = self._latched_fault
            self._latched_fault = None
            self._guard = SensorGuard()
            self._read_temperature()
            if self._fault:
                self._latched_fault = self._latched_fault or old_fault
                raise ValueError("Sensor readings are still invalid")
            await self._persist()
        await self._reconcile()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.hass.data.setdefault("hive_virtual_controllers", {})[self.entity_id] = self
        key = sha256(self._attr_unique_id.encode()).hexdigest()[:24]
        self._store = Store(self.hass, 1, "hive_virtual_" + key)
        try:
            saved = await self._store.async_load() or {}
            self._latched_fault = saved.get("fault")
            if saved.get("pending_until"):
                self._pending_until = dt_util.parse_datetime(saved["pending_until"])
                if self._pending_until is None or self._pending_until.tzinfo is None:
                    raise ValueError("Invalid saved boost deadline")
            for entity, (value, when) in saved.get("accepted", {}).items():
                parsed = dt_util.parse_datetime(when)
                if parsed is None or parsed.tzinfo is None or average([value]) is None:
                    raise ValueError("Invalid saved sensor reference")
                self._guard.accepted[entity] = (float(value), parsed)
        except Exception:
            self._storage_fault = True
            _LOGGER.exception("Could not load virtual thermostat safety state")
        if old := await self.async_get_last_state():
            # Restoring a target is safe; enabling heat requires an explicit action.
            try:
                target = float(old.attributes[ATTR_TEMPERATURE])
                if 7 <= target <= 30:
                    self._attr_target_temperature = target
            except (KeyError, ValueError, TypeError):
                pass
        self._watch()
        self.async_on_remove(async_track_time_interval(self.hass, self._tick, timedelta(seconds=30)))
        await self._reconcile()

    async def async_will_remove_from_hass(self):
        self._removed = True
        self.hass.data.get("hive_virtual_controllers", {}).pop(self.entity_id, None)
        if self._unwatch:
            self._unwatch()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._persist()
        await super().async_will_remove_from_hass()

    @callback
    def _changed(self, event):
        if self._removed:
            return
        task = self.hass.async_create_task(self._reconcile())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _tick(self, now):
        await self._reconcile()

    def _boosting(self):
        state = self.hass.states.get(self.config["hive_entity"])
        return state and state.attributes.get("preset_mode") == "boost"

    def _read_temperature(self):
        areas = self.config["areas"]
        self._area = self.config.get("default_area", next(iter(areas)))
        if self._slot:
            self._area = self._slot["area"]
        elif selector := self.config.get("active_area_entity"):
            selected = self.hass.states.get(selector)
            self._area = selected.state if selected else None
        if self._area not in areas:
            self._fault = "invalid_area"
            return None
        values = {}
        for entity_id in areas[self._area]:
            state = self.hass.states.get(entity_id)
            if state is None or (dt_util.utcnow() - state.last_reported).total_seconds() > self.config["sensor_timeout"] * 60:
                self._fault = f"missing_or_stale:{entity_id}"
                return None
            if report_entity := self.config["sensor_report_entities"].get(entity_id):
                report = self.hass.states.get(report_entity)
                try:
                    timestamp = dt_util.parse_datetime(report.state) if report else None
                except (ValueError, TypeError):
                    timestamp = None
                age = (dt_util.utcnow() - timestamp).total_seconds() if timestamp and timestamp.tzinfo else None
                if age is None or not 0 <= age <= self.config["sensor_timeout"] * 60:
                    self._fault = f"device_report_stale:{entity_id}"
                    return None
            value = average([state.state])
            unit = state.attributes.get("unit_of_measurement")
            if value is None or unit not in ("°C", "°F"):
                self._fault = f"invalid_reading:{entity_id}"
                return None
            values[entity_id] = TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS)
        rejection = self._guard.check(values, dt_util.utcnow(), self.config["sensor_min"],
            self.config["sensor_max"], self.config["max_sensor_jump"],
            self.config["jump_window"], self.config["max_sensor_spread"])
        if rejection:
            self._latched_fault = rejection
        self._fault = self._latched_fault
        if self._fault:
            return None
        if max(values.values()) >= self.config["high_temperature_cutoff"]:
            self._latched_fault = self._fault = "high_temperature_cutoff"
            return None
        return average(values.values())

    async def _reconcile(self):
        async with self._lock:
            if self._removed:
                return
            self._slot = schedule_slot(self.config["schedule"], dt_util.now()) if self.hvac_mode == HVACMode.AUTO else None
            schedule_fault = None
            if self.hvac_mode == HVACMode.AUTO and self.config.get("schedule_entity"):
                helper = self.hass.states.get(self.config["schedule_entity"])
                self._slot = None
                if helper and helper.state == "on":
                    temp = average([helper.attributes.get("temperature")])
                    area = helper.attributes.get("area")
                    if temp is not None and 7 <= temp <= 30 and area in self.config["areas"]:
                        self._slot = {"temperature": temp, "area": area}
                    else:
                        schedule_fault = "invalid_schedule_data"
                elif not helper or helper.state != "off":
                    schedule_fault = "schedule_unavailable"
            if self._slot:
                self._attr_target_temperature = self._slot["temperature"]
            self._attr_current_temperature = self._read_temperature()
            if schedule_fault:
                self._fault = schedule_fault
            self._demand = demand(self.hvac_mode in (HVACMode.HEAT, HVACMode.AUTO) and (self.hvac_mode != HVACMode.AUTO or self._slot is not None), self.current_temperature,
                                  self.target_temperature, self._demand, self.config["tolerance"])
            if schedule_fault or self._latched_fault or self._storage_fault:
                self._demand = False
            await self._persist()
            if self._storage_fault:
                self._demand = False
            hive = self.hass.states.get(self.config["hive_entity"])
            boosting = bool(self._boosting())
            if self._was_boosting and not boosting and hive and hive.state not in ("unknown", "unavailable"):
                deadline = dt_util.utcnow() + timedelta(minutes=self.config["cooldown_minutes"])
                self._pending_until = max(self._pending_until or deadline, deadline)
            if hive and hive.state not in ("unknown", "unavailable"):
                self._was_boosting = boosting
            if hive is None or hive.state in ("unknown", "unavailable"):
                self._status = "hive_unavailable"
            elif self._boosting():
                self._status = "boost_active_sensor_fault" if self._fault else "boost_active"
            elif "boost" not in hive.attributes.get("preset_modes", []):
                self._status = "unsupported_hive_entity"
            elif hive.attributes.get("preset_mode") != "none":
                self._status = "unknown_boost_state"
            else:
                self._status = "sensor_fault" if self.current_temperature is None else "controlling"
                try:
                    await self._apply(hive)
                except Exception:  # Service failures must not disable future retries.
                    self._status = "control_error"
                    _LOGGER.exception("Unable to control %s", self.config["hive_entity"])
            await self._persist()
            self.async_write_ha_state()
            for listener in list(self._listeners):
                listener()
            self._notify()

    def _notify(self):
        if not self._store:
            return
        from homeassistant.components import persistent_notification
        message = self.fault_message if self.config["notifications"] else None
        if message == self._notice:
            return
        self._notice = message
        notice_id = "hive_virtual_" + sha256(self._attr_unique_id.encode()).hexdigest()[:24]
        if message:
            persistent_notification.async_create(self.hass,
                f"{message}. New heating requests are blocked. Check Hive Heating and the sensors. Active Hive boosts are left to expire.",
                title=self.name + " needs attention", notification_id=notice_id)
        else:
            persistent_notification.async_dismiss(self.hass, notice_id)

    async def _apply(self, hive):
        unit = hive.attributes.get("temperature_unit", self.hass.config.units.temperature_unit)
        if unit != "°C":
            self._status = "unsupported_temperature_unit"
            return
        desired = TemperatureConverter.convert(self.config["idle_temperature"], UnitOfTemperature.CELSIUS, unit)
        if not hive.attributes.get("min_temp", desired) <= desired <= hive.attributes.get("max_temp", desired):
            self._status = "target_out_of_range"
            return
        if not hive.attributes.get("min_temp", 7) <= self.config["call_temperature"] <= hive.attributes.get("max_temp", 32):
            self._status = "target_out_of_range"
            return
        # Reserve a whole expiry interval even if the cloud accepted a command
        # but its reply was lost. Never renew a boost on stale unboosted data.
        now = dt_util.utcnow()
        if self._pending_until and now < self._pending_until:
            self._status = "waiting_for_boost_expiry"
            return
        if hive.last_reported < self._started or (now - hive.last_reported).total_seconds() > 120:
            self._status = "waiting_for_hive_refresh"
            return
        if self._boosting():
            return
        # Confirm low target BEFORE enabling manual mode; confirm manual mode
        # on another update BEFORE issuing any bounded demand.
        if hive.attributes.get("temperature") != desired:
            await self.hass.services.async_call("climate", "set_temperature", {
                "entity_id": self.config["hive_entity"], "temperature": desired}, blocking=True)
            self._status = "setting_safe_baseline"
            return
        if hive.state != HVACMode.HEAT:
            await self.hass.services.async_call("climate", "set_hvac_mode", {
                "entity_id": self.config["hive_entity"], "hvac_mode": HVACMode.HEAT}, blocking=True)
            self._status = "setting_safe_baseline"
            return
        if not self._demand:
            return
        minutes = self.config["boost_minutes"]
        self._pending_until = now + timedelta(minutes=minutes + self.config["cooldown_minutes"])
        # Durable reservation must succeed BEFORE sending a cloud request.
        if not await self._persist():
            self._status = "safety_storage_error"
            return
        if self._removed or self._boosting() or self.hvac_mode == HVACMode.OFF:
            return
        self._status = "starting_bounded_boost"
        await self.hass.services.async_call("hive", "boost_heating_on", {
            "entity_id": self.config["hive_entity"], "time_period": f"00:{minutes:02d}:00",
            "temperature": TemperatureConverter.convert(self.config["call_temperature"], UnitOfTemperature.CELSIUS, unit),
        }, blocking=True)

    @property
    def hvac_action(self):
        hive = self.hass.states.get(self.config["hive_entity"])
        if not hive or hive.state in ("unknown", "unavailable"):
            return None
        if self._boosting():
            return hive.attributes.get("hvac_action")
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        return hive.attributes.get("hvac_action", HVACAction.IDLE)

    @property
    def extra_state_attributes(self):
        return {"hive_virtual_controller": True, "controller_unique_id": self._attr_unique_id,
                "active_area": self._area, "control_status": self._status,
                "heat_requested": self._demand, "hive_entity": self.config["hive_entity"],
                "sensor_fault": self._fault, "fault_latched": self._latched_fault is not None,
                "schedule": self.config["schedule"], "schedule_active": self._slot,
                "boost_minutes": self.config["boost_minutes"],
                "fault_reason": self.fault_message, "schedule_entity": self.config.get("schedule_entity"),
                "cooldown_minutes": self.config["cooldown_minutes"],
                "retry_after": self._pending_until.isoformat() if self._pending_until else None}

    async def async_set_temperature(self, **kwargs):
        target = kwargs.get(ATTR_TEMPERATURE)
        if target is not None:
            target = float(target)
            if not 7 <= target <= 30:
                raise ValueError("Target must be between 7 and 30°C")
            self._attr_target_temperature = target
            # Setting a target is a manual hold until Auto is selected again.
            if self.hvac_mode == HVACMode.AUTO:
                self._attr_hvac_mode = HVACMode.HEAT
        if "hvac_mode" in kwargs:
            await self.async_set_hvac_mode(kwargs["hvac_mode"])
        else:
            await self._reconcile()

    async def async_set_hvac_mode(self, hvac_mode):
        if hvac_mode not in self.hvac_modes:
            raise ValueError("Only auto, heat and off are supported")
        if hvac_mode == HVACMode.AUTO and not self.config["schedule"] and not self.config.get("schedule_entity"):
            raise ValueError("Configure a schedule before selecting Auto")
        self._attr_hvac_mode = hvac_mode
        await self._reconcile()

    async def async_turn_on(self):
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self):
        await self.async_set_hvac_mode(HVACMode.OFF)
