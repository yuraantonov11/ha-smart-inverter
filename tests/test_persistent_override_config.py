import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hems.predictive_control import parse_predictive_options, DEFAULT_OPTIONS


class TestConfig(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(parse_predictive_options({}), DEFAULT_OPTIONS)

    def test_valid_explicit(self):
        self.assertEqual(parse_predictive_options({"predictive_default_mode": "Assist", "predictive_min_confidence_for_assist": .8})["predictive_default_mode"], "Assist")

    def test_invalid_options(self):
        for key, values in {
            "predictive_default_mode": ["Active", "assist", None],
            "predictive_night_window_start_hour": [-1, 24, True, 7, 23.5],
            "predictive_min_confidence_for_assist": [-.1, 1.1, float("nan"), float("inf"), True, "0.2"],
        }.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    parse_predictive_options({key: value})


if __name__ == "__main__": unittest.main()
