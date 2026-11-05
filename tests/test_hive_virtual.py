"""Offline decisions and service-boundary tests (not a Home Assistant runtime)."""
import asyncio
from datetime import datetime, timedelta, timezone
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import voluptuous as vol

PKG = "virtual_test_package"
package = ModuleType(PKG)
package.__path__ = [str(Path(__file__).parents[1] / "custom_components/hive_virtual")]
sys.modules[PKG] = package
control = importlib.import_module(PKG + ".control")


class Climate:
    name = property(lambda self: self._attr_name)
    hvac_mode = property(lambda self: self._attr_hvac_mode)
    hvac_modes = property(lambda self: self._attr_hvac_modes)
    current_temperature = property(lambda self: self._attr_current_temperature)
    target_temperature = property(lambda self: self._attr_target_temperature)
    def async_write_ha_state(self):
        pass

    async def async_added_to_hass(self):
        pass

    async def async_will_remove_from_hass(self):
        pass

    def async_on_remove(self, func):
        pass


class Restore:
    async def async_get_last_state(self):
        return None


class MemoryStore:
    records = {}

    def __init__(self, hass, version, key):
        self.key = key

    async def async_load(self):
        from copy import deepcopy
        return deepcopy(self.records.get(self.key))

    async def async_save(self, value):
        from copy import deepcopy
        self.records[self.key] = deepcopy(value)


def module(**kwargs):
    result = ModuleType("stub")
    result.__dict__.update(kwargs)
    return result


stubs = {
    "homeassistant": module(), "homeassistant.components": module(),
    "homeassistant.components.climate": module(
        # Home Assistant requires this YAML-only key. Keeping that requirement
        # here prevents config-entry validation from accidentally using it.
        PLATFORM_SCHEMA=vol.Schema({vol.Required("platform"): str}), ClimateEntity=Climate,
        ClimateEntityFeature=SimpleNamespace(TARGET_TEMPERATURE=1, TURN_ON=2, TURN_OFF=4),
        HVACMode=SimpleNamespace(HEAT="heat", OFF="off", AUTO="auto"),
        HVACAction=SimpleNamespace(HEATING="heating", IDLE="idle", OFF="off")),
    "homeassistant.const": module(ATTR_TEMPERATURE="temperature", UnitOfTemperature=SimpleNamespace(CELSIUS="°C")),
    "homeassistant.core": module(callback=lambda f: f),
    "homeassistant.helpers": module(),
    "homeassistant.helpers.config_validation": module(string=str, entity_id=str, boolean=bool, ensure_list=lambda v: v if isinstance(v, list) else [v], empty_config_schema=lambda domain: vol.Schema({vol.Optional(domain): {}}, extra=vol.ALLOW_EXTRA)),
    "homeassistant.helpers.event": module(async_track_state_change_event=lambda *a: lambda: None, async_track_time_interval=lambda *a: lambda: None),
    "homeassistant.helpers.restore_state": module(RestoreEntity=Restore),
    "homeassistant.helpers.storage": module(Store=MemoryStore),
    "homeassistant.util": module(),
    "homeassistant.util.dt": module(utcnow=lambda: datetime.now(timezone.utc), now=lambda: datetime.now(timezone.utc), parse_datetime=lambda value: datetime.fromisoformat(value) if value else None),
    "homeassistant.util.unit_conversion": module(TemperatureConverter=SimpleNamespace(
        convert=lambda v, source, target: v if source == target else (v - 32) * 5 / 9)),
}
stubs["homeassistant.helpers"].config_validation = stubs["homeassistant.helpers.config_validation"]
stubs["homeassistant.util"].dt = stubs["homeassistant.util.dt"]
with patch.dict(sys.modules, stubs):
    climate = importlib.import_module(PKG + ".climate")


class Flow:
    def __init_subclass__(cls, **kwargs):
        pass

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_show_menu(self, **kwargs):
        return {"type": "menu", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}

    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        pass


package.DOMAIN = "hive_virtual"
flow_stubs = {**stubs,
    "homeassistant.config_entries": module(ConfigFlow=Flow, OptionsFlow=Flow),
    "homeassistant.helpers.selector": module(**{key: (lambda *a, **k: lambda value: value)
        for key in ("EntitySelector", "SelectSelector", "NumberSelector", "TimeSelector", "BooleanSelector", "ObjectSelector")}),
}
flow_stubs["homeassistant"].config_entries = flow_stubs["homeassistant.config_entries"]
flow_stubs["homeassistant.helpers"].selector = flow_stubs["homeassistant.helpers.selector"]
with patch.dict(sys.modules, flow_stubs):
    flow_module = importlib.import_module(PKG + ".config_flow")


def state(value, **attributes):
    return SimpleNamespace(state=value, attributes=attributes, last_reported=datetime.now(timezone.utc))


class ControlTests(unittest.TestCase):
    def test_weekly_schedule_wraps_midnight_and_week(self):
        slots = [
            {"time": "06:30", "days": [0, 1, 2, 3, 4], "temperature": 20, "area": "Downstairs"},
            {"time": "22:00", "temperature": 18, "area": "Upstairs"},
        ]
        monday_early = datetime(2026, 11, 2, 5, 0)
        self.assertEqual(control.schedule_slot(slots, monday_early)["area"], "Upstairs")
        monday_boundary = datetime(2026, 11, 2, 6, 30)
        self.assertEqual(control.schedule_slot(slots, monday_boundary)["temperature"], 20)
        saturday = datetime(2026, 11, 7, 9, 0)
        self.assertEqual(control.schedule_slot(slots, saturday)["temperature"], 18)
        self.assertIsNone(control.schedule_slot([], monday_early))

    def test_sensor_guard_rejects_extremes_and_disagreement(self):
        guard = control.SensorGuard()
        now = datetime.now(timezone.utc)
        self.assertIsNone(guard.check({"a": 18, "b": 20}, now, 5, 35, 3, 10, 6))
        self.assertIn("out_of_range", guard.check({"a": -30}, now, 5, 35, 3, 10, 6))
        self.assertEqual(guard.check({"c": 10, "d": 24}, now, 5, 35, 3, 10, 6), "sensor_disagreement")
        self.assertEqual(guard.accepted["a"][0], 18)

    def test_averaging_requires_all_valid_sensors(self):
        self.assertEqual(control.average([18, "22"]), 20)
        for values in ([], [18, "unavailable"], [float("nan")], [float("inf")]):
            self.assertIsNone(control.average(values))

    def test_deadband_and_fault_shutdown(self):
        self.assertTrue(control.demand(True, 19, 20, False, .3))
        self.assertTrue(control.demand(True, 20, 20, True, .3))
        self.assertFalse(control.demand(True, 20, 20, False, .3))
        self.assertFalse(control.demand(True, 21, 20, True, .3))
        self.assertFalse(control.demand(True, None, 20, True, .3))
        self.assertFalse(control.demand(False, 15, 20, True, .3))


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        MemoryStore.records.clear()
        self.states = {
            "climate.hive": state("heat", preset_mode="none", preset_modes=["none", "boost"], temperature=7, temperature_unit="°C", min_temp=7, max_temp=32),
            "sensor.down": state("18", unit_of_measurement="°C"),
            "sensor.up": state("22", unit_of_measurement="°C"),
            "sensor.area": state("Downstairs"),
        }
        self.entity = climate.HiveVirtual({"name": "Test", "hive_entity": "climate.hive",
            "areas": {"Downstairs": ["sensor.down"], "Upstairs": ["sensor.up"]},
            "active_area_entity": "sensor.area", "target_temperature": 20,
            "idle_temperature": 7, "call_temperature": 32, "tolerance": .3, "sensor_timeout": 120})
        self.call = AsyncMock()
        self.entity._started -= timedelta(seconds=1)
        self.entity.hass = SimpleNamespace(states=SimpleNamespace(get=self.states.get), services=SimpleNamespace(async_call=self.call), config=SimpleNamespace(units=SimpleNamespace(temperature_unit="°C")))
        self.entity.hass.data = {}
        self.entity.entity_id = "climate.virtual"
        self.entity._notify = Mock()

    async def test_heat_then_area_change_stops_demand(self):
        await self.entity.async_turn_on()
        self.assertEqual(self.call.call_args.args[:2], ("hive", "boost_heating_on"))
        self.assertEqual(self.call.call_args.args[2]["temperature"], 32)
        self.assertEqual(self.call.call_args.args[2]["time_period"], "00:05:00")
        self.states["sensor.area"].state = "Upstairs"
        await self.entity._reconcile()
        self.assertFalse(self.entity._demand)
        self.assertEqual(self.call.call_count, 1)

    async def test_manual_boost_survives_target_off_and_sensor_fault(self):
        self.states["climate.hive"].attributes["preset_mode"] = "boost"
        await self.entity.async_turn_on()
        await self.entity.async_set_temperature(temperature=25)
        self.states["sensor.down"].state = "unavailable"
        await self.entity.async_turn_off()
        self.call.assert_not_called()
        self.assertEqual(self.entity.extra_state_attributes["control_status"], "boost_active_sensor_fault")

    async def test_resume_after_boost(self):
        self.states["climate.hive"].attributes["preset_mode"] = "boost"
        await self.entity.async_turn_on()
        self.call.assert_not_called()
        self.states["climate.hive"].attributes["preset_mode"] = "none"
        await self.entity._reconcile()
        self.call.assert_not_called()
        self.entity._pending_until -= timedelta(minutes=3)
        await self.entity._reconcile()
        self.assertEqual(self.call.call_args.args[2]["temperature"], 32)

    async def test_bad_sensor_lowers_existing_target(self):
        self.states["climate.hive"].attributes["temperature"] = 32
        self.states["sensor.down"].state = "unknown"
        await self.entity.async_turn_on()
        self.assertEqual(self.call.call_args.args[2]["temperature"], 7)
        self.assertFalse(self.entity._demand)
        self.assertEqual(self.call.call_count, 1)

    async def test_unavailable_hive_no_writes(self):
        self.states["climate.hive"].state = "unavailable"
        await self.entity.async_turn_on()
        self.call.assert_not_called()

    async def test_stale_heating_report_does_not_accumulate_runtime(self):
        self.states["climate.hive"].attributes["hvac_action"] = "heating"
        self.states["climate.hive"].last_reported -= timedelta(minutes=3)
        self.entity._runtime.on = True
        self.entity._runtime.last = datetime.now(timezone.utc).timestamp() - 30
        await self.entity._reconcile()
        self.assertIsNone(self.entity._runtime.on)
        self.assertEqual(self.entity._runtime.seconds, 0)

    async def test_stale_sensor_and_bad_area_stop_heat(self):
        self.states["climate.hive"].attributes["temperature"] = 32
        self.states["sensor.down"].last_reported -= timedelta(hours=3)
        await self.entity.async_turn_on()
        self.assertEqual(self.call.call_args.args[2]["temperature"], 7)
        self.states["sensor.area"].state = "unknown"
        await self.entity._reconcile()
        self.assertIsNone(self.entity.current_temperature)

    async def test_matching_target_does_not_repeat_write(self):
        await self.entity.async_turn_on()
        await self.entity._reconcile()
        self.assertEqual(self.call.call_count, 1)

    async def test_fahrenheit_sensor_converted_before_average(self):
        self.states["sensor.down"] = state("68", unit_of_measurement="°F")
        await self.entity.async_turn_on()
        self.assertEqual(self.entity.current_temperature, 20)
        self.call.assert_not_called()

    async def test_availability_mode_accepts_unchanged_but_rejects_unusable_sensor(self):
        self.entity.config["use_sensor_availability"] = True
        sensor = self.states["sensor.down"]
        sensor.last_reported -= timedelta(hours=3)
        await self.entity.async_turn_on()
        self.call.assert_awaited_once()
        self.assertTrue(self.entity._demand)
        self.assertEqual(self.entity.area_temperature("Downstairs"), 18)
        for value in ("unknown", "unavailable"):
            sensor.state = value
            await self.entity._reconcile()
            self.assertFalse(self.entity._demand)
            self.assertIsNone(self.entity.area_temperature("Downstairs"))
            self.assertEqual(self.entity.fault_message, "sensor_unavailable:sensor.down")
        sensor.state = "60"
        await self.entity._reconcile()
        self.assertEqual(self.entity.fault_message, "out_of_range:sensor.down")
        self.call.assert_awaited_once()

    async def test_partial_area_uses_remaining_sensor_then_leaves_heating_to_hive(self):
        self.entity.config.update(allow_partial_area=True, use_sensor_availability=True, preserve_hive_settings=True)
        self.entity.config["areas"]["Downstairs"].append("sensor.living_room")
        self.states["sensor.living_room"] = state("20", unit_of_measurement="°C")
        self.assertEqual(self.entity.area_temperature("Downstairs"), 19)
        self.states["sensor.living_room"].state = "unknown"
        await self.entity.async_turn_on()
        self.assertEqual(self.entity.current_temperature, 18)
        self.assertEqual(self.entity.area_temperature("Downstairs"), 18)
        self.assertTrue(self.entity._demand)
        self.call.assert_awaited_once()

        self.entity._pending_until -= timedelta(minutes=8)
        self.states["sensor.down"].state = "unavailable"
        await self.entity._reconcile()
        self.assertIsNone(self.entity.current_temperature)
        self.assertIsNone(self.entity.area_temperature("Downstairs"))
        self.assertEqual(self.entity.fault_message, "no_available_sensors:Downstairs")
        self.assertFalse(self.entity._demand)
        self.call.assert_awaited_once()

        self.states["sensor.living_room"].state = "17"
        await self.entity._reconcile()
        self.assertEqual(self.entity.current_temperature, 17)
        self.assertIsNone(self.entity.fault_message)
        self.assertEqual(self.call.await_count, 2)

        self.states["sensor.living_room"].state = "60"
        await self.entity._reconcile()
        self.assertEqual(self.entity.fault_message, "out_of_range:sensor.living_room")
        self.assertFalse(self.entity._demand)

    async def test_service_error_is_retried(self):
        self.call.side_effect = RuntimeError("offline")
        with self.assertLogs(climate.__name__, level="ERROR"):
            await self.entity.async_turn_on()
        self.assertEqual(self.entity.extra_state_attributes["control_status"], "control_error")
        self.call.side_effect = None
        await self.entity._reconcile()
        self.assertEqual(self.call.call_count, 1)  # Lost reply might still have started a boost.
        self.entity._pending_until -= timedelta(minutes=7)
        await self.entity._reconcile()
        self.assertEqual(self.call.call_count, 2)

    async def test_fault_latches_and_requires_off_on(self):
        self.states["sensor.down"].state = "-20"
        await self.entity.async_turn_on()
        self.call.assert_not_called()
        self.states["sensor.down"].state = "18"
        await self.entity._reconcile()
        self.assertTrue(self.entity.extra_state_attributes["fault_latched"])
        self.call.assert_not_called()
        await self.entity.async_turn_off()
        await self.entity.async_reset_fault()
        await self.entity.async_turn_on()
        self.assertEqual(self.call.call_count, 1)

    async def test_sudden_drop_cannot_request_heat(self):
        self.states["sensor.down"].state = "22"
        await self.entity.async_turn_on()
        self.states["sensor.down"].state = "12"
        await self.entity._reconcile()
        self.call.assert_not_called()
        self.assertIn("sudden_jump", self.entity.extra_state_attributes["sensor_fault"])

    async def test_baseline_is_confirmed_before_boost(self):
        self.states["climate.hive"].attributes["temperature"] = 32
        await self.entity.async_turn_on()
        self.assertEqual(self.call.call_args.args[:2], ("climate", "set_temperature"))
        self.assertEqual(self.call.call_args.args[2]["temperature"], 7)
        await self.entity._reconcile()
        self.assertTrue(all(call.args[0] == "climate" for call in self.call.call_args_list))
        self.states["climate.hive"].attributes["temperature"] = 7
        await self.entity._reconcile()
        self.assertEqual(self.call.call_args.args[0], "hive")

    async def test_stale_hive_cannot_start_boost(self):
        self.states["climate.hive"].last_reported -= timedelta(minutes=3)
        await self.entity.async_turn_on()
        self.call.assert_not_called()

    async def test_hive_managed_fallback_only_sends_timed_boosts(self):
        self.entity.config["preserve_hive_settings"] = True
        self.states["climate.hive"].attributes["temperature"] = 18
        await self.entity._reconcile()
        self.call.assert_not_called()
        await self.entity.async_turn_on()
        self.assertEqual(self.call.call_args.args[:2], ("hive", "boost_heating_on"))
        self.assertEqual(self.call.call_args.args[2]["time_period"], "00:05:00")
        self.states["climate.hive"].attributes.update(preset_mode="boost", temperature=32)
        await self.entity._reconcile()
        self.states["climate.hive"].attributes.update(preset_mode="none", temperature=19)
        self.states["sensor.down"].state = "unavailable"
        await self.entity._reconcile()
        await self.entity.async_turn_off()
        self.call.assert_awaited_once()

    async def test_warmer_fixed_fallback_and_ignored_native_fallback(self):
        config = climate.validate_config({**self.entity.config, "idle_temperature": 18})
        self.entity.config = config
        await self.entity.async_turn_on()
        self.assertEqual(self.call.call_args.args[:2], ("climate", "set_temperature"))
        self.assertEqual(self.call.call_args.args[2]["temperature"], 18)
        climate.validate_config({**config, "preserve_hive_settings": True, "call_temperature": 17})

    async def test_auto_schedule_and_manual_hold(self):
        now = datetime.now(timezone.utc)
        self.entity.config["schedule"] = [{"time": "00:00", "temperature": 21, "area": "Upstairs"}]
        await self.entity.async_set_hvac_mode("auto")
        self.assertEqual(self.entity.target_temperature, 21)
        self.assertEqual(self.entity.extra_state_attributes["active_area"], "Upstairs")
        self.call.assert_not_called()
        await self.entity.async_set_temperature(temperature=19)
        self.assertEqual(self.entity.hvac_mode, "heat")
        self.assertEqual(self.entity.target_temperature, 19)

    async def test_high_cutoff_latches_before_first_request(self):
        self.states["sensor.down"].state = "29"
        await self.entity.async_turn_on()
        self.call.assert_not_called()
        self.assertEqual(self.entity.extra_state_attributes["sensor_fault"], "high_temperature_cutoff")

    async def test_unknown_boost_state_no_commands(self):
        self.states["climate.hive"].attributes.pop("preset_mode")
        await self.entity.async_turn_on()
        self.call.assert_not_called()
        self.assertEqual(self.entity.extra_state_attributes["control_status"], "unknown_boost_state")

    async def test_boost_past_controller_deadline_is_never_renewed(self):
        await self.entity.async_turn_on()
        self.entity._pending_until -= timedelta(hours=1)
        self.states["climate.hive"].attributes["preset_mode"] = "boost"
        self.states["sensor.down"].state = "-30"
        await self.entity._reconcile()
        await self.entity.async_turn_off()
        self.assertEqual(self.call.call_count, 1)
        self.assertEqual(self.call.call_args.args[:2], ("hive", "boost_heating_on"))

    async def test_configuration_validation(self):
        config = climate.PLATFORM_SCHEMA({"platform": "hive_virtual", "name": "Example", "hive_entity": "climate.hive", "areas": {"Room": ["sensor.down"]},
            "schedule": [{"time": "06:00", "temperature": 20, "area": "Room"}]})
        add = lambda entities: None
        await climate.async_setup_platform(self.entity.hass, config, add)
        config["schedule"][0]["area"] = "Missing"
        with self.assertRaises(vol.Invalid):
            await climate.async_setup_platform(self.entity.hass, config, add)
        config["schedule"][0]["area"] = "Room"
        config["schedule"].append(config["schedule"][0])
        with self.assertRaises(vol.Invalid):
            await climate.async_setup_platform(self.entity.hass, config, add)

    async def test_reservation_saved_before_cloud_and_restored_after_restart(self):
        await self.entity.async_added_to_hass()
        async def check_saved(*args, **kwargs):
            if args[0] == "hive":
                saved = await self.entity._store.async_load()
                self.assertIsNotNone(saved["pending_until"])
        self.call.side_effect = check_saved
        await self.entity.async_turn_on()
        saved_deadline = self.entity._pending_until
        restarted = climate.HiveVirtual(self.entity.config)
        restarted.hass = self.entity.hass
        restarted.entity_id = "climate.virtual"
        restarted._notify = Mock()
        await restarted.async_added_to_hass()
        self.assertEqual(restarted._pending_until, saved_deadline)
        self.assertEqual(restarted.hvac_mode, "off")
        self.call.reset_mock()
        await restarted.async_turn_on()
        self.call.assert_not_called()

    async def test_latched_fault_survives_restart_and_enabling_heat(self):
        await self.entity.async_added_to_hass()
        self.states["sensor.down"].state = "-20"
        await self.entity._reconcile()
        self.states["sensor.down"].state = "18"
        restarted = climate.HiveVirtual(self.entity.config)
        restarted.hass = self.entity.hass
        restarted.entity_id = "climate.virtual"
        restarted._notify = Mock()
        await restarted.async_added_to_hass()
        await restarted.async_turn_on()
        self.assertTrue(restarted.extra_state_attributes["fault_latched"])
        self.call.assert_not_called()

    async def test_storage_failure_blocks_all_new_boosts(self):
        self.entity._store = SimpleNamespace(async_save=AsyncMock(side_effect=OSError("disk full")))
        with self.assertLogs(climate.__name__, level="ERROR"):
            await self.entity.async_turn_on()
        self.call.assert_not_called()
        self.assertTrue(self.entity._storage_fault)

    async def test_graceful_shutdown_can_flush_runtime_inside_save_interval(self):
        self.entity._store = MemoryStore(self.entity.hass, 1, "runtime_flush_test")
        await self.entity._persist()
        self.entity._runtime.seconds = 90
        await self.entity._persist()
        self.assertEqual((await self.entity._store.async_load())["runtime"]["seconds"], 0)
        await self.entity._persist(force=True)
        self.assertEqual((await self.entity._store.async_load())["runtime"]["seconds"], 90)

    async def test_native_schedule_selects_area_and_gaps_stop_demand(self):
        self.entity.config["schedule_entity"] = "schedule.heating"
        self.states["schedule.heating"] = state("on", temperature=23, area="Upstairs")
        await self.entity.async_set_hvac_mode("auto")
        self.assertEqual(self.entity.target_temperature, 23)
        self.assertEqual(self.entity.extra_state_attributes["active_area"], "Upstairs")
        self.assertTrue(self.entity._demand)
        self.states["schedule.heating"].state = "off"
        await self.entity._reconcile()
        self.assertFalse(self.entity._demand)

    async def test_invalid_native_schedule_blocks_heating(self):
        self.entity.config["schedule_entity"] = "schedule.heating"
        self.states["schedule.heating"] = state("on", temperature="nan", area="Upstairs")
        await self.entity.async_set_hvac_mode("auto")
        self.call.assert_not_called()
        self.assertEqual(self.entity.fault_message, "invalid_schedule_data")

    async def test_live_settings_keep_mode_fault_and_deadline(self):
        await self.entity.async_turn_on()
        deadline = self.entity._pending_until
        self.entity._latched_fault = "checked_sensor_needed"
        await self.entity.async_update_config({**self.entity.config, "boost_minutes": 10})
        self.assertEqual(self.entity.hvac_mode, "heat")
        self.assertEqual(self.entity._pending_until, deadline)
        self.assertEqual(self.entity._latched_fault, "checked_sensor_needed")
        self.assertEqual(self.call.call_count, 1)

    async def test_device_report_timestamp_overrides_republished_fresh_state(self):
        self.entity.config["sensor_report_entities"] = {"sensor.down": "sensor.report"}
        self.states["sensor.report"] = state((datetime.now(timezone.utc) - timedelta(hours=3)).isoformat())
        await self.entity.async_turn_on()
        self.call.assert_not_called()
        self.assertIn("device_report_stale", self.entity.fault_message)
        self.states["sensor.report"].state = "invalid"
        await self.entity._reconcile()
        self.call.assert_not_called()

    async def test_fault_reset_requires_off_and_valid_readings(self):
        await self.entity.async_turn_on()
        with self.assertRaises(ValueError):
            await self.entity.async_reset_fault()
        await self.entity.async_turn_off()
        self.states["sensor.down"].state = "-30"
        with self.assertRaises(ValueError):
            await self.entity.async_reset_fault()

    async def test_removed_controller_cannot_start_requests(self):
        self.entity._removed = True
        await self.entity.async_turn_on()
        self.call.assert_not_called()

    async def test_notifications_only_change_on_fault_transitions(self):
        notice = module(async_create=Mock(), async_dismiss=Mock())
        component = module(persistent_notification=notice)
        self.entity._store = object()
        self.entity._fault = "sensor_missing"
        with patch.dict(sys.modules, {"homeassistant": module(components=component), "homeassistant.components": component}):
            climate.HiveVirtual._notify(self.entity)
            climate.HiveVirtual._notify(self.entity)
            notice.async_create.assert_called_once()
            self.entity._fault = None
            climate.HiveVirtual._notify(self.entity)
            notice.async_dismiss.assert_called_once()
    async def test_boost_appearing_during_mode_change_prevents_target_write(self):
        self.states["climate.hive"].state = "auto"
        async def mode_changed(*args, **kwargs):
            self.states["climate.hive"].attributes["preset_mode"] = "boost"
        self.call.side_effect = mode_changed
        await self.entity.async_turn_on()
        self.assertEqual(self.call.call_count, 1)
        self.assertEqual(self.call.call_args.args[1], "set_hvac_mode")


class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.flow = flow_module.HiveVirtualConfigFlow()
        self.flow.hass = SimpleNamespace(states=SimpleNamespace(async_all=lambda domain: []))
        self.input = {"name": "House virtual heating", "hive_entity": "climate.hive",
            "downstairs_sensors": ["sensor.down"], "upstairs_sensors": ["sensor.up"],
            "default_area": "Downstairs"}

    async def test_user_setup_creates_valid_controller(self):
        form = await self.flow.async_step_user()
        self.assertEqual(form["step_id"], "user")
        result = await self.flow.async_step_user(self.input)
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(self.flow.unique_id, "climate.hive")
        self.assertEqual(result["data"]["areas"]["Upstairs"], ["sensor.up"])
        self.assertEqual(len(result["data"]["schedule"]), 4)
        self.assertEqual(result["data"]["boost_minutes"], 5)

    async def test_empty_floor_selection_rejected(self):
        self.input["upstairs_sensors"] = []
        result = await self.flow.async_step_user(self.input)
        self.assertEqual(result["errors"]["upstairs_sensors"], "missing_sensors")
        preserved = result["data_schema"]({})
        self.assertEqual(preserved["hive_entity"], "climate.hive")
        self.assertEqual(preserved["downstairs_sensors"], ["sensor.down"])

    async def test_existing_yaml_controller_rejected(self):
        self.flow.hass.states.async_all = lambda domain: [state("off", hive_entity="climate.hive")]
        result = await self.flow.async_step_user(self.input)
        self.assertEqual(result["reason"], "controller_exists")

    async def _options(self):
        created = await self.flow.async_step_user(self.input)
        options = flow_module.HiveVirtualOptionsFlow()
        options.config_entry = SimpleNamespace(data=created["data"], options={})
        return options

    async def test_schedule_form_edit_and_duplicate_validation(self):
        options = await self._options()
        selected = await options.async_step_edit_period({"period": "0"})
        self.assertEqual(selected["step_id"], "period")
        result = await options.async_step_period({"time": "08:30:00", "temperature": 21, "area": "Upstairs", "days": ["0", "1"]})
        self.assertEqual(result["errors"]["base"], "invalid_schedule")
        result = await options.async_step_period({"time": "07:00:00", "temperature": 21, "area": "Upstairs", "days": ["0", "1"]})
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["schedule"][0]["days"], [0, 1])
        self.assertEqual(result["data"]["schedule"][0]["time"], "07:00")
        self.assertEqual(options.config_entry.data["schedule"][0]["time"], "06:30")

    async def test_add_delete_and_safety_validation(self):
        options = await self._options()
        added = await options.async_step_add_period({"time": "12:00:00", "temperature": 19, "area": "Downstairs", "days": ["5", "6"]})
        self.assertEqual(len(added["data"]["schedule"]), 5)
        removed = await options.async_step_delete_period({"period": "0"})
        self.assertEqual(len(removed["data"]["schedule"]), 3)
        result = await options.async_step_safety({"sensor_min": 30, "sensor_max": 20})
        self.assertEqual(result["errors"]["sensor_min"], "sensor_limits_reversed")
        result = await options.async_step_safety({"boost_minutes": 2})
        self.assertEqual(result["data"]["boost_minutes"], 2)
        empty = await options.async_step_add_period({"time": "12:00:00", "temperature": 19, "area": "Downstairs", "days": []})
        self.assertEqual(empty["errors"]["base"], "invalid_schedule")

    async def test_entry_load_reload_and_unload(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("virtual_entry_lifecycle", Path(package.__path__[0]) / "__init__.py")
        lifecycle = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, flow_stubs):
            spec.loader.exec_module(lifecycle)
        manager = SimpleNamespace(async_forward_entry_setups=AsyncMock(), async_reload=AsyncMock(), async_unload_platforms=AsyncMock(return_value=True))
        hass = SimpleNamespace(config_entries=manager, data={})
        registered = []
        created = await self.flow.async_step_user(self.input)
        entry = SimpleNamespace(entry_id="entry1", data=created["data"], options={}, async_on_unload=lambda listener: registered.append(listener), add_update_listener=lambda listener: listener)
        panel = module(async_setup_panel=AsyncMock(), async_remove_panel=Mock())
        with patch.dict(sys.modules, {"virtual_entry_lifecycle": lifecycle, "virtual_entry_lifecycle.climate": climate, "virtual_entry_lifecycle.panel": panel}):
            self.assertTrue(await lifecycle.async_setup_entry(hass, entry))
            manager.async_forward_entry_setups.assert_awaited_once_with(entry, ["climate", "binary_sensor", "button", "sensor"])
            entry.runtime_data.async_update_config = AsyncMock()
            await registered[0](hass, entry)
            entry.runtime_data.async_update_config.assert_awaited_once()
            manager.async_reload.assert_not_called()
            self.assertTrue(await lifecycle.async_unload_entry(hass, entry))
            manager.async_unload_platforms.assert_awaited_once_with(entry, ["climate", "binary_sensor", "button", "sensor"])
            panel.async_remove_panel.assert_called_once()

    async def test_climate_entry_applies_saved_options(self):
        created = await self.flow.async_step_user(self.input)
        entry = SimpleNamespace(data=created["data"], options={"boost_minutes": 3})
        entry.runtime_data = climate.HiveVirtual(climate.validate_config({**entry.data, **entry.options}))
        added = []
        await climate.async_setup_entry(self.flow.hass, entry, added.extend)
        self.assertEqual(added[0].config["boost_minutes"], 3)
        self.assertEqual(added[0].hvac_mode, "off")

    async def test_ui_config_does_not_require_yaml_platform(self):
        result = await self.flow.async_step_user(self.input)
        self.assertEqual(result["type"], "create_entry")
        self.assertNotIn("platform", result["data"])
        with self.assertRaises(vol.Invalid):
            climate.PLATFORM_SCHEMA(result["data"])
        self.assertEqual(climate.validate_config(result["data"])["hive_entity"], "climate.hive")

    async def test_fallback_error_identifies_field(self):
        options = await self._options()
        result = await options.async_step_safety({"idle_temperature": 10, "call_temperature": 9})
        self.assertEqual(result["errors"], {"idle_temperature": "fallback_not_lower"})


if __name__ == "__main__":
    unittest.main()
