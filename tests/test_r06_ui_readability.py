"""Chromium contracts for the existing production dashboard cards."""
from __future__ import annotations

import unittest

from playwright.sync_api import Page

from test_r06_browser_playwright import _Fixture


class ExistingCardReadability(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = _Fixture()
        cls.fixture.start_browser()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.stop()

    def _page(self) -> tuple[Page, list[str]]:
        page, errors = self.fixture.new_page({"width": 1200, "height": 1000})
        page.goto(self.fixture.base_url)
        return page, errors

    def test_pv_comparison_energy_has_two_places_and_missing_fact_is_unavailable(self) -> None:
        page, errors = self._page()
        view = page.evaluate("""() => {
          const today = new Intl.DateTimeFormat('en-CA', {timeZone:'UTC',year:'numeric',month:'2-digit',day:'2-digit'})
            .formatToParts(new Date()).reduce((o,p) => (o[p.type]=p.value,o), {});
          const date = `${today.year}-${today.month}-${today.day}`;
          const forecast = {hourly_forecast_date:date,hourly_forecast_w:Array(24).fill(100),total_kwh:12.3456};
          const actual = {points:[]};
          const card = document.createElement('pv-comparison-card');
          card.setConfig({entity:'sensor.actual',forecast_entity:'sensor.forecast'});
          card.hass = {config:{time_zone:'UTC'},states:{'sensor.actual':{attributes:actual},'sensor.forecast':{attributes:forecast}}};
          document.body.append(card);
          return {text:card.innerText,forecast:forecast.total_kwh,actualPoints:actual.points.length};
        }""")
        self.assertIn("12,35 кВт·год", view["text"])
        self.assertIn("Дані недоступні", view["text"])
        self.assertNotIn("0 Вт", view["text"])
        self.assertEqual(view["forecast"], 12.3456)
        self.assertEqual(view["actualPoints"], 0)
        self.assertEqual(errors, [])

    def test_k_flow_formats_energy_and_currency_without_mutating_source_values(self) -> None:
        page, errors = self._page()
        result = page.evaluate("""() => {
          const values = {
            grid_import:'1.234', grid_export:'2.5', today_pv:'1.235', savings:'5.678'
          };
          const states = {};
          for (const [key,value] of Object.entries(values)) states[`sensor.${key}`] = {state:value,attributes:{unit_of_measurement:'kWh'}};
          states['sensor.savings'].attributes.unit_of_measurement = 'UAH';
          const card = document.createElement('k-flow-card');
          card.setConfig({grid_import_energy:'sensor.grid_import',grid_export_energy:'sensor.grid_export',
            today_pv:'sensor.today_pv',daily_savings:'sensor.savings'});
          card.hass = {states};
          document.body.append(card);
          const root = card.shadowRoot || card;
          const text = id => root.querySelector(`#${id}`)?.textContent;
          return {values,text:{gridImport:text('gridImportVal'),gridExport:text('gridExportVal'),
            todayPv:text('invTodayPv'),savings:text('invDailySavings')},source:states};
        }""")
        self.assertEqual(result["text"], {
            "gridImport": "▼ 1.23 kWh", "gridExport": "▲ 2.50 kWh",
            "todayPv": "1.24 kWh", "savings": "5.68 ₴",
        })
        self.assertEqual(result["source"]["sensor.grid_import"]["state"], "1.234")
        self.assertEqual(result["source"]["sensor.grid_export"]["state"], "2.5")
        self.assertEqual(result["source"]["sensor.today_pv"]["state"], "1.235")
        self.assertEqual(result["source"]["sensor.savings"]["state"], "5.678")
        self.assertEqual(errors, [])

    def test_k_flow_missing_measurements_are_unavailable_not_zero(self) -> None:
        page, errors = self._page()
        values = page.evaluate("""() => {
          const card = document.createElement('k-flow-card');
          card.setConfig({});
          card.hass = {states:{}};
          document.body.append(card);
          const root = card.shadowRoot || card;
          const text = id => root.querySelector(`#${id}`)?.textContent;
          return {grid:text('gridImportVal'),export:text('gridExportVal'),temperature:text('invTempFlow'),
            todayPv:text('invTodayPv'),savings:text('invDailySavings'),soc:text('fcBattVal')};
        }""")
        self.assertEqual(values["grid"], "Дані недоступні")
        self.assertEqual(values["export"], "Дані недоступні")
        self.assertEqual(values["temperature"], "Дані недоступні")
        self.assertEqual(values["todayPv"], "Дані недоступні")
        self.assertEqual(values["savings"], "Дані недоступні")
        self.assertEqual(values["soc"], "Дані недоступні")
        self.assertTrue(all(value != "0" for value in values.values()))
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
