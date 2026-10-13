"""Test the «📊 Підсумки» markdown card in the «Історія» view.

Юра: «Історія → Підсумки → Загальна енергія. Там залишається
«Невідомо». Звір entity ID та джерело показника. Якщо даних
справді немає — покажи зрозуміле «Немає даних», не підставляй
нуль.»
"""
from __future__ import annotations

import unittest
from pathlib import Path

import yaml
from jinja2 import Environment

REPO = Path(__file__).resolve().parents[1]

ENT_TOTAL = "sensor.garazh_smart_solar_inverter_total_cumulative_energy_kwh"
ENT_MONTH = "sensor.garazh_smart_solar_inverter_monthly_pv_energy_kwh"
ENT_YEAR = "sensor.garazh_smart_solar_inverter_yearly_pv_energy_kwh"


def _summary_card_template() -> str:
    """Find the «📊 Підсумки» markdown card content. It lives
    in the live dashboard, NOT in dashboard.yaml — the
    deployer injects it via ``_replace_summary_entities_card``.
    We pull the content from the deploy script directly so
    the test exercises the same template that gets deployed."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "deploy_script_for_test", REPO / "scripts" / "deploy_dashboard_no_token.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._SUMMARY_CARD_TEMPLATE


def _render(
    *,
    total: str | None = "0",
    month: str | None = "0",
    year: str | None = "0",
) -> str:
    env = Environment(autoescape=False)

    def _state(entity):
        if entity == ENT_TOTAL:
            return total
        if entity == ENT_MONTH:
            return month
        if entity == ENT_YEAR:
            return year
        return None

    env.globals["states"] = _state
    return env.from_string(_summary_card_template()).render()


class SummaryCardTests(unittest.TestCase):
    """Юра fixed: the entities card «📊 Підсумки» in the
    «Історія» view must show «Немає даних» for the total
    sensor when the API endpoint returned an empty response
    and the coordinator kept the previous cache (so
    ``native_value`` is ``None`` and the entity state is
    ``unknown``). It must NOT show «Невідомо» and must NOT
    show 0.00."""

    def test_total_unknown_shows_no_data(self) -> None:
        rendered = _render(total="unknown", month="120.50", year="1800.25")
        self.assertIn("Немає даних", rendered)
        # The other entities (month, year) are real numbers.
        self.assertIn("120.50", rendered)
        self.assertIn("1800.25", rendered)
        # Diagnostic note for the operator.
        self.assertIn("API ще не повернув значення", rendered)

    def test_total_unavailable_shows_no_data(self) -> None:
        rendered = _render(total="unavailable", month="50.0", year="100.0")
        self.assertIn("Немає даних", rendered)
        self.assertIn("50.00", rendered)
        self.assertIn("100.00", rendered)

    def test_total_none_string_shows_no_data(self) -> None:
        """Jinja renders Python ``None`` as the string ``None``
        (or HA returns ``none``); both must be treated as the
        auto state with no data, not as 0."""
        rendered = _render(total="None", month="0", year="0")
        self.assertIn("Немає даних", rendered)

    def test_total_empty_string_shows_no_data(self) -> None:
        rendered = _render(total="", month="0", year="0")
        self.assertIn("Немає даних", rendered)

    def test_total_real_value_is_formatted(self) -> None:
        """When the API returns a real cumulative value, the
        card shows it as ``X.XX кВт·год`` — never as
        «Невідомо» or «Немає даних»."""
        rendered = _render(total="308.735", month="18.716", year="308.735")
        self.assertIn("308.74", rendered)
        self.assertIn("18.72", rendered)
        self.assertNotIn("Невідомо", rendered)

    def test_no_zero_substitution_for_unknown(self) -> None:
        """Юра fixed: «не підставляй нуль». When the sensor is
        unknown, the card must NOT print ``0.00`` for the
        total — that would be a fake-zero."""
        rendered = _render(total="unknown", month="0", year="0")
        # The total row should literally be «Немає даних».
        self.assertIn("Немає даних", rendered)
        # And the total row should NOT have a 0.00 value
        # substituted where the data is missing. We assert
        # the total row by scanning the rendered output.
        lines = rendered.splitlines()
        total_line = next(l for l in lines if "Загальна" in l)
        self.assertNotIn("0.00", total_line)


if __name__ == "__main__":
    unittest.main()
