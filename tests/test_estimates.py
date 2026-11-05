"""Units, midnight, unknown gaps and cost attribution boundaries."""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("estimates_under_test", Path(__file__).parents[1] / "custom_components/hive_virtual/estimates.py")
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)


class EstimateTests(unittest.TestCase):
    def test_delayed_gas_uses_exact_window_and_one_sample_per_day(self):
        source = ("climate.hive", "sensor.edf", 0)
        learning = model.DelayedGasLearning(source)
        # Use 23-hour days to exercise matching the supplied window length.
        for index in range(3):
            start = index * 86400
            end = start + 46 * 1800
            charges = [{"start": datetime.fromtimestamp(at, timezone.utc).isoformat(),
                        "end": datetime.fromtimestamp(at + 1800, timezone.utc).isoformat()}
                       for at in range(start, end, 1800)]
            self.assertEqual(model.reported_gas_period(charges), (start, end))
            self.assertIsNone(model.reported_gas_period(charges[:-1]))
            learning.match(start, end, 24, end)
            self.assertNotIn(str(start), learning.days)
            for now in range(start, end + 1, 60):
                learning.update(now, now < start + 7200)
            learning.match(start, end, 24, end + 86400)
            learning.match(start, end, 24, end + 86400)
            self.assertEqual(len(learning.days), index + 1)
        self.assertEqual(learning.kw, 12)
        self.assertEqual(model.DelayedGasLearning(source, learning.saved()).kw, 12)

    def test_learning_uses_complete_runs_and_restores_only_matching_meter(self):
        source = ("climate.hive", "sensor.gas", 0)
        learning = model.GasLearning(source)
        for index, kwh in enumerate((1, 3, 1.2)):
            start = index * 600
            learning.update(start, False, None)
            learning.update(start + 30, True, (start, 50, "kWh"))
            learning.update(start + 60, True, (start + 60, 50, "kWh"))
            learning.update(start + 150, True, (start + 60, 50, "kWh"))
            learning.update(start + 240, True, (start + 240, 50 + kwh, "kWh"))
            learning.update(start + 270, False, (start + 270, 90, "kWh"))
            if index < 2:
                self.assertIsNone(learning.kw)
        self.assertEqual(len(learning.runs), 3)
        self.assertAlmostEqual(learning.kw, 24)
        restored = model.GasLearning(source, learning.saved())
        self.assertAlmostEqual(restored.kw, 24)
        self.assertIsNone(restored.last)
        self.assertEqual(model.GasLearning(("climate.hive", "sensor.other", 0), learning.saved()).runs, [])

    def test_learning_discards_meter_resets_and_missing_heating_reports(self):
        for bad_action, bad_reading in ((True, (240, 0, "kWh")), (None, (240, 51, "kWh")), (True, None)):
            learning = model.GasLearning(("climate.hive", "sensor.gas", 0))
            learning.update(0, False, None)
            learning.update(30, True, (0, 49, "kWh"))
            learning.update(60, True, (60, 50, "kWh"))
            learning.update(150, True, (60, 50, "kWh"))
            learning.update(240, True, (240, 51, "kWh"))
            if bad_reading is not None:
                bad_reading = (270, bad_reading[1], bad_reading[2])
            learning.update(270, bad_action, bad_reading)
            learning.update(300, False, None)
            self.assertEqual(learning.runs, [])

    def test_units_are_explicit(self):
        self.assertEqual(model.energy_kwh(1500, "Wh"), 1.5)
        self.assertEqual(model.energy_kwh(1.5, "MWh"), 1500)
        self.assertIsNone(model.energy_kwh(2, "m³"))
        self.assertEqual(model.energy_kwh(2, "m³", 11.2), 22.4)
        self.assertIsNone(model.energy_kwh(20, "unrecognised"))

    def test_invalid_inputs_are_not_zero_cost(self):
        for value in ("unavailable", "unknown", "nan", "inf", -1, None):
            self.assertIsNone(model.number(value))
            self.assertIsNone(model.price(value))

    def test_pence_and_currency_units(self):
        self.assertEqual(model.price(6.5, True), .065)
        self.assertEqual(model.price(.065, False), .065)
        self.assertEqual(model.price(0, True), 0)

    def test_measured_cost_and_standing_not_double_counted(self):
        result = model.estimate(2, 12, .07, measured_energy=50, measured_cost=4, standing=.3)
        self.assertAlmostEqual(result["heating_cost"], 1.68)
        self.assertEqual(result["household_variable_cost"], 4)
        self.assertEqual(result["standing_charge"], .3)
        self.assertEqual(result["household_gas_kwh"], 50)

    def test_unconfigured_estimates_remain_unknown(self):
        result = model.estimate(2, 0, None)
        self.assertIsNone(result["heating_kwh"])
        self.assertIsNone(result["heating_cost"])
        self.assertIsNone(result["household_variable_cost"])

    def test_runtime_uses_prior_report_and_omits_unknown_and_long_gap(self):
        meter = model.DailyRuntime()
        meter.update(1000, "2026-11-04", 1000, True)
        meter.update(1030, "2026-11-04", 1030, False)
        self.assertEqual(meter.seconds, 30)
        meter.update(1060, "2026-11-04", 1060, True)
        meter.update(1090, "2026-11-04", 1090, None)
        self.assertEqual(meter.seconds, 30)
        meter.update(1120, "2026-11-04", 1120, True)
        meter.update(2000, "2026-11-04", 2000, True)
        self.assertEqual(meter.seconds, 30)

    def test_midnight_and_restart_never_fill_unobserved_time(self):
        meter = model.DailyRuntime({"day": "2026-11-03", "seconds": 3600})
        meter.update(1000, "2026-11-03", 86390, True)
        meter.update(1030, "2026-11-04", 20, True)
        self.assertEqual(meter.seconds, 20)
        restored = model.DailyRuntime(meter.saved())
        restored.update(1050, "2026-11-04", 40, True)
        self.assertEqual(restored.seconds, 20)
