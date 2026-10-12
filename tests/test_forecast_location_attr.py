"""Regression test: the
``ForecastTomorrowSensor`` exposes a
``forecast_location`` attribute so the
dashboard can show the user WHERE the
forecast is targeted. The previous code
exposed no such attribute; a misconfigured
station was only visible deep in the
journal.

We use AST extraction (rather than importing
``sensor.py`` wholesale) so the test runs
without a Home Assistant install — the
test suite forbids importing HA.
"""
from __future__ import annotations

import ast
import sys
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _extract_extra_state_attributes() -> str:
    """AST-extract the body of
    ``ForecastTomorrowSensor.extra_state_attributes``
    from ``sensor.py`` so we can exec it
    in a controlled namespace. This is the
    same approach the T02 / T16 test
    suites use for cross-file assertions.

    ``ast.unparse`` preserves the
    ``@property`` decorator and the
    ``-> dict[str, Any] | None`` return
    annotation. The decorator is harmless
    when exec'd inside a plain function
    wrapper; the ``Any`` annotation is a
    type comment and we add ``Any`` /
    ``dict`` / ``str`` to the namespace so
    the unparsed string compiles cleanly
    inside the wrapper.
    """
    src = (REPO_ROOT / "sensor.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    target = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.ClassDef)
                and node.name == "ForecastTomorrowSensor"):
            for sub in node.body:
                if (isinstance(sub, ast.FunctionDef)
                        and sub.name == "extra_state_attributes"):
                    target = sub
                    break
            break
    assert target is not None, (
        "ForecastTomorrowSensor.extra_state_attributes "
        "must be defined in sensor.py"
    )
    # Strip the ``@property`` decorator
    # so the wrapped exec compiles to a
    # plain function (the wrapper provides
    # the binding).
    target.decorator_list = []
    return ast.unparse(target)


class ForecastLocationAttributeTests(unittest.TestCase):
    """The ``ForecastTomorrowSensor.extra_state_attributes``
    property must include a
    ``forecast_location`` dict that mirrors
    ``coordinator._pv_learning.identity``
    plus ``coordinator._identity_source``.
    """

    def _exec(self, coord, body_src: str):
        # The body uses ``self.coordinator.<attr>``
        # — make ``self`` a thin wrapper whose
        # ``coordinator`` is the stub.
        class _StubSelf:
            pass

        self_obj = _StubSelf()
        self_obj.coordinator = coord
        ns = {
            "self": self_obj,
            "any": lambda *a, **k: True,
            "getattr": getattr,
            "WMO_WEATHER_MAP": {
                0: ("sunny", "\u2600\ufe0f", "\u042f\u0441\u043d\u043e"),
                1: ("partlycloudy", "\U0001f324\ufe0f", "\u041f\u0435\u0440\u0435\u0432\u0430\u0436\u043d\u043e \u044f\u0441\u043d\u043e"),
                2: ("partlycloudy", "\u26c5", "\u0425\u043c\u0430\u0440\u043d\u043e \u0437 \u043f\u0440\u043e\u044f\u0441\u043d\u0435\u043d\u043d\u044f\u043c\u0438"),
                3: ("cloudy", "\u2601\ufe0f", "\u0425\u043c\u0430\u0440\u043d\u043e"),
            },
            "Any": object,  # type-annotation placeholder
            "dict": dict,
            "str": str,
        }
        # AST.unparse preserves the inner ``def extra_state_attributes(self):``
        # which would never be called if we just exec'd the body as a flat
        # function. Rename it to ``_inner`` and call it with the stub ``self``
        # so the actual return value flows up.
        renamed = body_src.replace(
            "def extra_state_attributes(self) -> dict[str, Any] | None:",
            "def _inner(self) -> dict[str, Any] | None:",
            1,
        ).replace(
            "def extra_state_attributes(self):",
            "def _inner(self):",
            1,
        )
        wrapped = "def _f(self):\n" + textwrap.indent(renamed, "    ") + "\n    return _inner(self)\n"
        exec(compile(wrapped, "<forecast-location>", "exec"), ns)
        return ns["_f"](self_obj)

    def _build_coordinator(self, *, identity: dict,
                           source: str | None,
                           hourly: list):
        from datetime import datetime, timezone

        class _StubLearning:
            pass

        class _StubCoordinator:
            def _pv_local_now(self):
                return datetime.now(timezone.utc)

        coord = _StubCoordinator()
        coord.hourly_forecast_today = hourly
        coord.hourly_weather_today = []
        coord.hourly_radiation_today = []
        coord.weather_tomorrow_code = 0
        if source is not None:
            coord._identity_source = source
        coord._pv_learning = _StubLearning()
        coord._pv_learning.identity = dict(identity)
        return coord

    def test_sensor_exposes_forecast_location_with_hourly(self) -> None:
        """When the sensor has hourly data,
        the ``forecast_location`` dict is
        present alongside the existing
        ``hourly_forecast_w`` field.
        """
        body_src = _extract_extra_state_attributes()
        coord = self._build_coordinator(
            identity={"timezone": "Europe/Kyiv",
                      "latitude": 49.83, "longitude": 24.0},
            source="ha",
            hourly=[10.0, 20.0],
        )
        attrs = self._exec(coord, body_src)
        self.assertIsNotNone(attrs)
        self.assertIn("forecast_location", attrs)
        loc = attrs["forecast_location"]
        self.assertEqual(loc["source"], "ha")
        self.assertEqual(loc["latitude"], 49.83)
        self.assertEqual(loc["longitude"], 24.0)
        self.assertEqual(loc["timezone"], "Europe/Kyiv")

    def test_sensor_exposes_forecast_location_without_hourly(self) -> None:
        """Even when ``hourly_forecast_today``
        is empty (first refresh, transient
        network failure) the
        ``forecast_location`` dict MUST be
        exposed — the user needs to see
        WHERE the integration is targeting
        even if no forecast row has been
        built yet.
        """
        body_src = _extract_extra_state_attributes()
        coord = self._build_coordinator(
            identity={"timezone": "Europe/Kyiv",
                      "latitude": 49.0, "longitude": 31.0},
            source="entry",
            hourly=[],
        )
        attrs = self._exec(coord, body_src)
        self.assertIsNotNone(
            attrs,
            msg=(
                "The sensor must still expose "
                "forecast_location when no hourly "
                "forecast is available yet — "
                "the operator needs to see the "
                "site identity at all times"
            ),
        )
        self.assertIn("forecast_location", attrs)
        loc = attrs["forecast_location"]
        self.assertEqual(loc["source"], "entry")
        self.assertEqual(loc["latitude"], 49.0)
        self.assertEqual(loc["longitude"], 31.0)
        self.assertEqual(loc["timezone"], "Europe/Kyiv")

    def test_source_unknown_when_coordinator_lacks_field(self) -> None:
        """If the coordinator does not have
        ``_identity_source`` (legacy code
        or partial restart), the attribute
        still serialises with
        ``source=unknown`` so the dashboard
        can show «невідомо» instead of
        crashing.
        """
        body_src = _extract_extra_state_attributes()
        coord = self._build_coordinator(
            identity={"timezone": "Europe/Kyiv",
                      "latitude": 49.83, "longitude": 24.0},
            source=None,
            hourly=[1.0, 2.0],
        )
        attrs = self._exec(coord, body_src)
        self.assertIsNotNone(attrs)
        self.assertEqual(
            attrs["forecast_location"]["source"],
            "unknown",
        )


class DashboardForecastLocationTests(unittest.TestCase):
    """The dashboard markdown block
    displaying the forecast location must
    exist and use ``forecast_location`` as
    its source.
    """

    def test_dashboard_has_forecast_location_markdown(self) -> None:
        yaml_text = (REPO_ROOT / "dashboard.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "forecast_location",
            yaml_text,
            msg=(
                "dashboard.yaml must reference the "
                "new forecast_location attribute "
                "so the operator can see WHERE "
                "the forecast is targeted"
            ),
        )
        self.assertIn(
            "Прогноз · фактична місцевість",
            yaml_text,
            msg=(
                "dashboard.yaml must include the "
                "human-readable heading "
                "'Прогноз · фактична місцевість'"
            ),
        )
        # The markdown references the
        # forecast_tomorrow sensor — that
        # is the sensor that now exposes
        # ``forecast_location``.
        self.assertIn(
            "sensor.smart_solar_inverter_forecast_tomorrow",
            yaml_text,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)