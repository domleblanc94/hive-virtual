"""Units, midnight, unknown gaps and cost attribution boundaries."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("estimates_under_test", Path(__file__).parents[1] / "custom_components/hive_virtual/estimates.py")
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)


class EstimateTests(unittest.TestCase):
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
