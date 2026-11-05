"""Scheduled room control using bounded Hive boosts."""
import asyncio
from datetime import datetime, timedelta
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
from .estimates import DailyRuntime, DelayedGasLearning, GasLearning, energy_kwh, estimate, number, price, reported_gas_period

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
    vol.Optional("cost", default={}): vol.Schema({
        vol.Optional("gas_energy_today_entity"): cv.entity_id,
        vol.Optional("gas_meter_entity"): cv.entity_id,
        vol.Optional("gas_reported_entity"): cv.entity_id,
        vol.Optional("learn_from_gas_meter", default=False): cv.boolean,
        vol.Optional("gas_cost_today_entity"): cv.entity_id,
        vol.Optional("unit_rate_entity"): cv.entity_id,
        vol.Optional("standing_charge_entity"): cv.entity_id,
        vol.Optional("currency", default="GBP"): vol.Match(r"^[A-Z]{3}$"),
        vol.Optional("rate_in_pence", default=False): cv.boolean,
        vol.Optional("cost_in_pence", default=False): cv.boolean,
        vol.Optional("boiler_input_kw", default=0): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        vol.Optional("gas_kwh_per_m3", default=0): vol.All(vol.Coerce(float), vol.Range(min=0, max=20)),
    }),
    vol.Optional("target_temperature", default=20): vol.All(vol.Coerce(float), vol.Range(min=7, max=30)),
    vol.Optional("call_temperature", default=32): vol.All(vol.Coerce(float), vol.Range(min=7, max=32)),
    vol.Optional("idle_temperature", default=7): vol.All(vol.Coerce(float), vol.Range(min=7, max=30)),
    vol.Optional("preserve_hive_settings", default=False): cv.boolean,
    vol.Optional("tolerance", default=0.3): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=2)),
    vol.Optional("sensor_timeout", default=120): vol.All(vol.Coerce(int), vol.Range(min=1)),
    vol.Optional("use_sensor_availability", default=False): cv.boolean,
    vol.Optional("allow_partial_area", default=False): cv.boolean,
    vol.Optional("minimum_area_sensors", default={}): {cv.string: vol.All(vol.Coerce(int), vol.Range(min=1))},
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
    cost = config["cost"]
    meter_key = "gas_meter_entity" if cost.get("gas_meter_entity") else "gas_energy_today_entity"
    meter = cost.get(meter_key)
    if cost["learn_from_gas_meter"] and not (meter or cost.get("gas_reported_entity")):
        raise vol.Invalid("Choose a live gas meter, Gas usage today, or a delayed gas sensor", path=["cost", meter_key])
    if cost.get("gas_reported_entity") and not cost["gas_reported_entity"].startswith("sensor."):
        raise vol.Invalid("Choose a delayed gas sensor", path=["cost", "gas_reported_entity"])
    if meter and (cost["learn_from_gas_meter"] or meter_key == "gas_meter_entity") and not meter.startswith("sensor."):
        raise vol.Invalid("Gas meter must be a sensor", path=["cost", meter_key])
    if config.get("default_area", next(iter(config["areas"]))) not in config["areas"]:
        raise vol.Invalid("default_area must name a configured area")
    for area, minimum in config["minimum_area_sensors"].items():
        if minimum > len(set(config["areas"].get(area, []))):
            raise vol.Invalid("invalid_sensor_minimum", path=["minimum_area_sensors", area])
    if not config["preserve_hive_settings"] and config["idle_temperature"] >= config["call_temperature"]:
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
    """Request bounded boosts without cancelling active boosts."""

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
        self._area_sensors = {}
        self._hive_fallback_status = None
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
        self._sensor_notice = {}
        self._tasks = set()
        self._was_boosting = False
        self._runtime = DailyRuntime()
        self.config.setdefault("notifications", True)
        self.config.setdefault("cooldown_minutes", 2)
        self.config.setdefault("sensor_report_entities", {})
        self.config.setdefault("cost", {"currency": "GBP", "rate_in_pence": False, "cost_in_pence": False,
                                      "boiler_input_kw": 0, "gas_kwh_per_m3": 0})
        self._gas_learning = GasLearning(self._gas_source())
        self._delayed_gas = DelayedGasLearning(self._delayed_source())
        self._reported_gas = None

    def _sensor_state_fault(self, state):
        if state is None or state.state in ("unknown", "unavailable"):
            return "sensor_unavailable"
        if (not self.config.get("use_sensor_availability", False)
                and (dt_util.utcnow() - state.last_reported).total_seconds() > self.config["sensor_timeout"] * 60):
            return "missing_or_stale"
        return None

    def area_temperature(self, area):
        values = []
        for entity in dict.fromkeys(self.config["areas"][area]):
            state = self.hass.states.get(entity)
            if fault := self._sensor_state_fault(state):
                if fault == "sensor_unavailable" and self.config.get("allow_partial_area", False):
                    continue
                return None
            value = average([state.state])
            unit = state.attributes.get("unit_of_measurement")
            if value is None or unit not in ("°C", "°F"):
                return None
            value = TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS)
            if not self.config["sensor_min"] <= value <= self.config["sensor_max"]:
                return None
            values.append(value)
        return average(values) if len(values) >= self.minimum_sensors(area) else None

    def minimum_sensors(self, area):
        return self.config.get("minimum_area_sensors", {}).get(area, 1)

    @property
    def sensor_fallback_status(self):
        return self._hive_fallback_status

    def cost_estimates(self):
        config = self.config["cost"]
        def source(key):
            return self.hass.states.get(config.get(key, ""))
        gas, cost, rate, standing = (source(key) for key in (
            "gas_energy_today_entity", "gas_cost_today_entity", "unit_rate_entity", "standing_charge_entity"))
        learner = self._delayed_gas if self._delayed_gas.source else self._gas_learning
        learned = learner.kw if config.get("learn_from_gas_meter") else None
        if config.get("gas_energy_today_entity") == config.get("gas_reported_entity"):
            gas = None
        return estimate(self._runtime.seconds / 3600, learned or config["boiler_input_kw"],
            price(rate.state, config["rate_in_pence"]) if rate else None,
            energy_kwh(gas.state, gas.attributes.get("unit_of_measurement"), config["gas_kwh_per_m3"]) if gas else None,
            price(cost.state, config["cost_in_pence"]) if cost else None,
            price(standing.state, config["cost_in_pence"]) if standing else None)

    def _gas_source(self):
        cost = self.config["cost"]
        if entity := cost.get("gas_meter_entity") or cost.get("gas_energy_today_entity"):
            return self.config["hive_entity"], entity, cost["gas_kwh_per_m3"]
        return None

    def _delayed_source(self):
        cost = self.config["cost"]
        if entity := cost.get("gas_reported_entity"):
            return self.config["hive_entity"], entity, cost["gas_kwh_per_m3"]
        return None

    def _learn_gas(self, timestamp, heating):
        if self._delayed_gas.source:
            _, entity, factor = self._delayed_gas.source
            self._delayed_gas.update(timestamp, heating)
            meter = self.hass.states.get(entity)
            self._reported_gas = None
            if meter:
                period = reported_gas_period(meter.attributes.get("charges"))
                energy = energy_kwh(meter.state, meter.attributes.get("unit_of_measurement"), factor)
                if period and energy is not None and period[1] <= timestamp:
                    start, end = period
                    self._reported_gas = {"date": datetime.fromtimestamp(start, dt_util.now().tzinfo).date().isoformat(), "kwh": energy}
                    if self.config["cost"].get("learn_from_gas_meter"):
                        self._delayed_gas.match(start, end, energy, timestamp)
            return
        if not self.config["cost"].get("learn_from_gas_meter"):
            return
        _, entity, factor = self._gas_learning.source
        meter = self.hass.states.get(entity)
        reading = None
        if meter:
            unit = meter.attributes.get("unit_of_measurement")
            energy = energy_kwh(meter.state, unit, factor)
            if energy is not None:
                reading = meter.last_changed.timestamp(), energy, unit
        self._gas_learning.update(timestamp, heating, reading)

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
                "hvac_mode": self.hvac_mode,
                "target_temperature": self.target_temperature,
                "runtime": self._runtime.saved(),
                "gas_learning": self._gas_learning.saved(),
                "delayed_gas": self._delayed_gas.saved(),
                "pending_until": self._pending_until.isoformat() if self._pending_until else None,
                "accepted": {key: [value, when.isoformat()] for key, (value, when) in self._guard.accepted.items()}}

    async def _persist(self, force=False):
        if self._storage_fault:
            return False
        if self._store is None:
            return True  # Before entity setup; production commands begin after loading.
        data = self._safety_data()
        if data == self._saved:
            return True
        # Save mode changes and other important transitions immediately.
        # Throttle routine temperature and runtime snapshots.
        if not force and self._saved and self._last_saved_at and (dt_util.utcnow() - self._last_saved_at).total_seconds() < 300:
            immediate = ("fault", "pending_until", "gas_learning", "hvac_mode", "target_temperature")
            if all(data[key] == self._saved.get(key) for key in immediate):
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
        watched.update(value for key, value in self.config["cost"].items() if key.endswith("_entity"))
        self._unwatch = async_track_state_change_event(self.hass, watched, self._changed)

    async def async_update_config(self, config):
        async with self._lock:
            was_learning = self.config["cost"].get("learn_from_gas_meter", False)
            self.config = validate_config(config)
            if self._gas_learning.source != self._gas_source() or was_learning != self.config["cost"]["learn_from_gas_meter"]:
                self._gas_learning = GasLearning(self._gas_source(), self._gas_learning.saved())
            if self._delayed_gas.source != self._delayed_source():
                self._delayed_gas = DelayedGasLearning(self._delayed_source())
                self._reported_gas = None
                self._gas_learning = GasLearning(self._gas_source(), self._gas_learning.saved())
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
            self._runtime = DailyRuntime(saved.get("runtime"))
            self._gas_learning = GasLearning(self._gas_source(), saved.get("gas_learning"))
            self._delayed_gas = DelayedGasLearning(self._delayed_source(), saved.get("delayed_gas"))
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
        if not self._storage_fault:
            old = await self.async_get_last_state() if "hvac_mode" not in saved else None
            mode = old.state if old else saved.get("hvac_mode")
            target = number(old.attributes.get(ATTR_TEMPERATURE) if old else saved.get("target_temperature"))
            valid_target = target is not None and 7 <= target <= 30
            if valid_target:
                self._attr_target_temperature = target
            if (mode == HVACMode.AUTO and self._has_schedule()) or (mode == HVACMode.HEAT and valid_target):
                self._attr_hvac_mode = mode
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
        await self._persist(force=True)
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
        self._area_sensors = {}
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
        blocking_fault = None
        for entity_id in areas[self._area]:
            state = self.hass.states.get(entity_id)
            if fault := self._sensor_state_fault(state):
                self._area_sensors[entity_id] = {"status": fault}
                if fault != "sensor_unavailable" or not self.config.get("allow_partial_area", False):
                    blocking_fault = blocking_fault or f"{fault}:{entity_id}"
                continue
            if report_entity := self.config["sensor_report_entities"].get(entity_id):
                report = self.hass.states.get(report_entity)
                try:
                    timestamp = dt_util.parse_datetime(report.state) if report else None
                except (ValueError, TypeError):
                    timestamp = None
                age = (dt_util.utcnow() - timestamp).total_seconds() if timestamp and timestamp.tzinfo else None
                if age is None or not 0 <= age <= self.config["sensor_timeout"] * 60:
                    self._area_sensors[entity_id] = {"status": "device_report_stale"}
                    blocking_fault = blocking_fault or f"device_report_stale:{entity_id}"
                    continue
            value = average([state.state])
            unit = state.attributes.get("unit_of_measurement")
            if value is None or unit not in ("°C", "°F"):
                self._area_sensors[entity_id] = {"status": "invalid_reading"}
                blocking_fault = blocking_fault or f"invalid_reading:{entity_id}"
                continue
            values[entity_id] = TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS)
            self._area_sensors[entity_id] = {"status": "excluded", "temperature": values[entity_id]}
        if blocking_fault:
            self._fault = blocking_fault
            return None
        if not values:
            self._fault = f"no_available_sensors:{self._area}"
            return None
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
        if len(values) < self.minimum_sensors(self._area):
            self._fault = f"too_few_sensors:{self._area}"
            return None
        for entity_id in values:
            self._area_sensors[entity_id]["status"] = "used"
        return average(values.values())

    def _sensor_fallback_status(self, hive, fresh):
        if self.current_temperature is not None or self.hvac_mode == HVACMode.OFF:
            return None
        if not fresh or hive.state in ("unknown", "unavailable"):
            return "unconfirmed"
        if hive.attributes.get("preset_mode") == "boost":
            return "boost_active"
        if hive.attributes.get("preset_mode") != "none":
            return "unconfirmed"
        if self.config.get("preserve_hive_settings"):
            return "hive_settings"
        unit = hive.attributes.get("temperature_unit", self.hass.config.units.temperature_unit)
        if unit == "°C" and hive.state == HVACMode.HEAT and hive.attributes.get("temperature") == self.config["idle_temperature"]:
            return "fixed_target"
        return "unconfirmed"

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
            fresh = hive and 0 <= (dt_util.utcnow() - hive.last_reported).total_seconds() <= 120
            self._hive_fallback_status = self._sensor_fallback_status(hive, fresh)
            action = hive.attributes.get("hvac_action") if fresh and hive.state not in ("unknown", "unavailable") else None
            on = True if action == "heating" else False if action in ("idle", "off") else None
            now, local = dt_util.utcnow(), dt_util.now()
            self._runtime.update(now.timestamp(), local.date().isoformat(),
                                 local.hour * 3600 + local.minute * 60 + local.second, on)
            self._learn_gas(now.timestamp(), on)
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
        fault = self.fault_message
        alert = None
        if self.config["notifications"]:
            previous = self._sensor_notice.get(self._area)
            if self.sensor_fallback_status:
                self._sensor_notice[self._area] = "fallback"
                detail = {
                    "boost_active": "Waiting for the active Hive boost to end.",
                    "hive_settings": "Hive is using its own mode and target.",
                    "fixed_target": "Hive is using the fixed fallback target.",
                    "unconfirmed": "Hive fallback could not be confirmed.",
                }[self.sensor_fallback_status]
                alert = ("sensor", f"{self._area}: sensor control paused. {detail} Reason: {fault}.")
            elif fault:
                alert = ("fault", f"{fault}. New heating requests are blocked. Check Hive Heating and the sensors. Active Hive boosts are left to expire.")
            elif self.hvac_mode != HVACMode.OFF and self.current_temperature is not None:
                used = sum(reading["status"] == "used" for reading in self._area_sensors.values())
                if used == 1 and len(self._area_sensors) > 1:
                    recovered = previous in ("fallback", "single_recovery")
                    self._sensor_notice[self._area] = "single_recovery" if recovered else "single"
                    if recovered:
                        alert = ("single_recovery", f"{self._area}: sensor control recovered with only one temperature sensor.")
                    else:
                        alert = ("sensor", f"{self._area}: heating is using only one temperature sensor.")
                else:
                    self._sensor_notice.pop(self._area, None)
                    if previous:
                        alert = ("recovery", f"{self._area}: temperature sensor control recovered. The virtual thermostat can request heating again.")
                    elif self._notice and self._notice[0] == "recovery":
                        alert = self._notice
        else:
            self._sensor_notice.clear()
        if alert == self._notice:
            return
        self._notice = alert
        notice_id = "hive_virtual_" + sha256(self._attr_unique_id.encode()).hexdigest()[:24]
        if alert:
            title = "sensor control recovered" if alert[0] in ("recovery", "single_recovery") else "needs attention"
            persistent_notification.async_create(self.hass, alert[1],
                title=f"{self.name}: {title}", notification_id=notice_id)
        else:
            persistent_notification.async_dismiss(self.hass, notice_id)

    async def _apply(self, hive):
        unit = hive.attributes.get("temperature_unit", self.hass.config.units.temperature_unit)
        if unit != "°C":
            self._status = "unsupported_temperature_unit"
            return
        preserve = self.config.get("preserve_hive_settings", False)
        if preserve and not self._demand:
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
        if not preserve:
            # Confirm the fixed target and manual mode before requesting a boost.
            desired = self.config["idle_temperature"]
            if not hive.attributes.get("min_temp", desired) <= desired <= hive.attributes.get("max_temp", desired):
                self._status = "target_out_of_range"
                return
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
                "area_sensors": self._area_sensors, "sensor_fallback_status": self._hive_fallback_status,
                "minimum_area_sensors": (self.minimum_sensors(self._area) if self.config.get("allow_partial_area")
                                         else len(self._area_sensors)),
                "heat_requested": self._demand, "hive_entity": self.config["hive_entity"],
                "sensor_fault": self._fault, "fault_latched": self._latched_fault is not None,
                "schedule": self.config["schedule"], "schedule_active": self._slot,
                "boost_minutes": self.config["boost_minutes"],
                "preserve_hive_settings": self.config.get("preserve_hive_settings", False),
                "fault_reason": self.fault_message, "schedule_entity": self.config.get("schedule_entity"),
                "cooldown_minutes": self.config["cooldown_minutes"],
                "temperature_areas": list(self.config["areas"]),
                "daily_heating_hours": round(self._runtime.seconds / 3600, 4),
                "cost_estimates": self.cost_estimates(), "currency": self.config["cost"]["currency"],
                "cost_sources": self.config["cost"],
                "learned_gas_kw": self._gas_learning.kw, "gas_learning_runs": len(self._gas_learning.runs),
                "reported_gas": self._reported_gas, "delayed_gas_kw": self._delayed_gas.kw,
                "gas_learning_days": len(self._delayed_gas.days),
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
        if hvac_mode == HVACMode.AUTO and not self._has_schedule():
            raise ValueError("Configure a schedule before selecting Auto")
        self._attr_hvac_mode = hvac_mode
        await self._reconcile()

    def _has_schedule(self):
        return bool(self.config["schedule"] or self.config.get("schedule_entity"))

    async def async_turn_on(self):
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self):
        await self.async_set_hvac_mode(HVACMode.OFF)
