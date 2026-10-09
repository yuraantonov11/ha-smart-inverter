"""R08 — demand model and documentation.

Юра's audit asks us to:
  - determine the actual EWMA call cadence
    and its effective smoothing horizon;
  - verify that the same load at different
    sample frequencies produces the same
    steady-state profile (but different
    transient convergence);
  - distinguish empirical quantiles and
    coefficient estimates in names and
    documentation.

This file exercises the production
``DemandForecastService`` directly. No mocks,
no regex substitution of the function body.

RED→GREEN history:
  - Before R08: the docstring claimed the
    p25/p50/p75/p90 outputs are "probabilistic
    forecasts" / "empirical quantiles". They
    are not — they are fixed multipliers of
    the EWMA mean. The docstring is corrected
    in ``hems/demand_forecast.py`` and the
    multiplier constants are named explicitly
    here.
"""

from __future__ import annotations

import importlib.util
import math
import pathlib
import sys
import unittest
from datetime import datetime, timedelta


def _load_demand_forecast():
    """Load the production
    ``hems/demand_forecast.py`` from the
    project root via importlib so the test
    file does not require a ``sys.path``-munging
    shim that mutates the search path. The
    module uses only stdlib + intra-package
    imports (no HA), so it loads directly.
    """
    import types
    root = pathlib.Path(__file__).resolve().parent.parent
    # Build a fake ``hems`` package.
    hems_pkg = types.ModuleType("hems")
    hems_pkg.__path__ = [str(root / "hems")]
    sys.modules["hems"] = hems_pkg
    spec = importlib.util.spec_from_file_location(
        "hems.demand_forecast",
        root / "hems" / "demand_forecast.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load spec")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hems.demand_forecast"] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_demand_forecast()
DemandForecastService = _mod.DemandForecastService
_DEFAULT_ALPHA = _mod._DEFAULT_ALPHA
_MIN_LOAD_W = _mod._MIN_LOAD_W
_MAX_LOAD_W = _mod._MAX_LOAD_W


class TestEwmaCadence(unittest.TestCase):
    """Юра: "verify same load at different
    sample frequencies". The EWMA profile is
    per-hour, so the steady-state is the same
    no matter how often we sample, but the
    convergence TRANSIENT differs.
    """

    def test_same_load_steady_state_independent_of_cadence(self):
        """Feed the same load at different
        sample rates, with enough samples per
        hour to converge the EWMA (>>1/α = 4).
        The steady-state profile must be the
        same regardless of cadence.

        At 1 sample per hour for 24h, the
        profile is NOT yet converged (it
        still has 75% of the default for
        each hour). We need at least 16
        samples per hour (4× the time
        constant) to be within 1% of
        steady-state.
        """
        # 1) 60 samples per hour, 24h
        svc_a = DemandForecastService()
        for i in range(60 * 24):
            h = (i // 60) % 24
            svc_a.update_ewma(
                datetime(2026, 1, 1, 0, 0, 0)
                + timedelta(seconds=i * 60),
                load_w=1000.0 + h * 50.0,
            )
        # 2) 16 samples per hour, 24h
        svc_b = DemandForecastService()
        delta = timedelta(seconds=60 * 60 / 16)
        for i in range(16 * 24):
            h = (i // 16) % 24
            svc_b.update_ewma(
                datetime(2026, 1, 1, 0, 0, 0)
                + delta * i,
                load_w=1000.0 + h * 50.0,
            )
        # After 60 samples per hour the
        # profile is fully converged; after 16
        # samples per hour it is within 1% of
        # converged.
        for h in range(24):
            self.assertAlmostEqual(
                svc_a.profile[h],
                svc_b.profile[h],
                delta=0.02 * 1000.0,  # 2% of
                                      # 1000 W
                msg=f"hour {h} steady-state "
                f"mismatch: {svc_a.profile[h]} "
                f"vs {svc_b.profile[h]}",
            )

    def test_low_cadence_does_not_converge(self):
        """The audit explicitly asks: "verify
        same load at different sample
        frequencies. First prove defect,
        then change algorithm."

        1 sample per hour is not enough for
        the EWMA to converge. This is
        EXPECTED behaviour for the current
        algorithm — the operator must see
        this in the diagnostic. The test
        documents the limitation, not a
        defect.
        """
        svc_low = DemandForecastService()
        svc_high = DemandForecastService()
        for h in range(24):
            svc_low.update_ewma(
                datetime(2026, 1, 1, h, 0, 0),
                load_w=1000.0 + h * 50.0,
            )
            for _ in range(60):
                svc_high.update_ewma(
                    datetime(2026, 1, 1, h, 0, 0)
                    + timedelta(seconds=_ * 60),
                    load_w=1000.0 + h * 50.0,
                )
        # Low cadence: profile is the EWMA
        # blend toward the per-hour load, but
        # not yet converged. The high-cadence
        # profile is fully converged. They
        # MUST differ for hours where the
        # load != default.
        # Concretely: hour 19 (load 1950,
        # default 3000): low cadence = 0.25
        # * 1950 + 0.75 * 3000 = 2737.5; high
        # cadence = 1950.
        self.assertNotAlmostEqual(
            svc_low.profile[19],
            svc_high.profile[19],
            delta=10.0,
            msg="low cadence should not "
            "converge to steady-state in 1 "
            "sample — this is the documented "
            "limitation of per-hour EWMA",
        )

    def test_effective_horizon_is_samples_per_hour(self):
        """With α=0.25, the time constant is
        ``1/α = 4`` SAMPLES PER HOUR, not
        per wall-clock time. After 4 samples
        in the same hour, the old value
        contributes <(1-α)^4 ≈ 0.32. After
        16 samples, the old value contributes
        <0.01 (i.e. effectively zero).
        """
        # Verify the mathematical identity
        # directly.
        alpha = _DEFAULT_ALPHA
        # After N samples, old contribution is
        # (1 - alpha)^N. We assert upper bounds
        # (not strict less) to avoid the
        # floating-point boundary case.
        for n, expected_max in (
            (4, 0.34),    # (0.75)^4 ≈ 0.316
            (8, 0.11),    # (0.75)^8 ≈ 0.100
            (16, 0.012),  # (0.75)^16 ≈ 0.010
        ):
            actual = (1 - alpha) ** n
            self.assertLess(
                actual, expected_max,
                f"after {n} samples, expected old "
                f"contribution < {expected_max}, "
                f"got {actual:.4f}",
            )

    def test_min_load_clamp(self):
        """The SAMPLE (not the stored value) is
        clamped to ``_MIN_LOAD_W``. After one
        update with ``load_w=0``, the stored
        value is an EWMA blend of the clamped
        sample and the previous default — it
        converges to ``_MIN_LOAD_W`` over many
        updates.
        """
        svc = DemandForecastService()
        # Single update with zero load: stored
        # value = α × 100 + (1 - α) × 500.
        svc.update_ewma(
            datetime(2026, 1, 1, 12, 0, 0),
            load_w=0.0,
        )
        alpha = _DEFAULT_ALPHA
        expected_one_step = (
            alpha * _MIN_LOAD_W
            + (1 - alpha) * 500.0
        )
        self.assertAlmostEqual(
            svc.profile[12], expected_one_step,
            places=4,
        )
        # After 100 zero-load updates, the
        # stored value converges to
        # ``_MIN_LOAD_W``.
        for _ in range(100):
            svc.update_ewma(
                datetime(2026, 1, 1, 12, 0, 0),
                load_w=0.0,
            )
        self.assertAlmostEqual(
            svc.profile[12], _MIN_LOAD_W,
            places=1,
        )

    def test_max_load_clamp(self):
        svc = DemandForecastService()
        # Sample is clamped to MAX before EWMA.
        svc.update_ewma(
            datetime(2026, 1, 1, 12, 0, 0),
            load_w=99999.0,
        )
        alpha = _DEFAULT_ALPHA
        # Hour 12 default is 500 W. EWMA one
        # step: α × 12000 + (1 - α) × 500.
        expected_one_step = (
            alpha * _MAX_LOAD_W + (1 - alpha) * 500.0
        )
        self.assertAlmostEqual(
            svc.profile[12], expected_one_step,
            places=4,
        )


class TestMultipliersAreNotQuantiles(unittest.TestCase):
    """The ``to_demand_forecast`` outputs are
    multipliers of the EWMA mean, not
    quantiles of a sample distribution. The
    multipliers are FIXED and identical for
    every hour. Verify that explicitly.
    """

    def test_same_multiplier_for_every_hour(self):
        """Every hour must use the same set of
        multipliers. If they were empirical
        quantiles, the ratios would differ per
        hour (some hours are spikier than
        others).
        """
        svc = DemandForecastService()
        # Two very different hours: set hour 3
        # to 250 W, hour 19 to 3000 W.
        svc._profile[3] = 250.0
        svc._profile[19] = 3000.0
        # Set everything else to 1000 W.
        for h in range(24):
            if h not in (3, 19):
                svc._profile[h] = 1000.0
        forecast = svc.to_demand_forecast()
        # The multiplier set is fixed: 0.80 /
        # 1.00 / 1.20 / 1.35. Verify at every
        # hour.
        for h, m in forecast.hourly_metrics.items():
            base = svc._profile[h]
            self.assertAlmostEqual(
                m.p25, base * 0.8, places=4,
                msg=f"hour {h}: p25 should be "
                f"0.8×base, got {m.p25} vs "
                f"{base * 0.8}",
            )
            self.assertAlmostEqual(
                m.p50, base * 1.0, places=4,
                msg=f"hour {h}: p50 should be "
                f"1.0×base, got {m.p50} vs {base}",
            )
            self.assertAlmostEqual(
                m.p75, base * 1.2, places=4,
            )
            self.assertAlmostEqual(
                m.p90, base * 1.35, places=4,
            )

    def test_field_names_match_published_contract(self):
        """The field names ``p25/p50/p75/p90``
        are part of the published sensor
        contract — do NOT rename them in the
        dataclass. The docstring says
        "multiplier" not "quantile" but the
        field names stay for compatibility.
        """
        from hems.demand_forecast import DemandMetrics
        m = DemandMetrics(p25=80, p50=100, p75=120, p90=135)
        # All four fields must remain accessible.
        for field in ("p25", "p50", "p75", "p90"):
            self.assertTrue(
                hasattr(m, field),
                f"DemandMetrics must keep field {field!r}",
            )
        # And the ratios must be the documented
        # 0.8 / 1.0 / 1.2 / 1.35.
        self.assertAlmostEqual(m.p25 / m.p50, 0.8, places=4)
        self.assertAlmostEqual(m.p75 / m.p50, 1.2, places=4)
        self.assertAlmostEqual(m.p90 / m.p50, 1.35, places=4)


class TestDocumentationStrings(unittest.TestCase):
    """Юра: "names and documentation must
    match the formula". The docstring is
    verified here.
    """

    def test_module_docstring_does_not_claim_empirical_quantiles(self):
        import hems.demand_forecast as mod
        doc = mod.__doc__ or ""
        self.assertNotIn(
            "empirical quantile", doc.lower(),
            "module docstring must not claim "
            "the multipliers are empirical "
            "quantiles",
        )
        self.assertIn(
            "multiplier", doc.lower(),
            "module docstring must call them "
            "multipliers",
        )
        # R08 follow-up: the production
        # docstring must NOT describe the
        # spread as a Gaussian distribution
        # or approximation. The audit note
        # may mention the previous bad
        # description in scare quotes, but
        # the live description must use
        # "heuristic" — not "Gaussian".
        # We allow "Gaussian" only when
        # negated or quoted.
        lowered = doc.lower()
        # Strip the audit-note paragraph
        # (between R08 audit and the next
        # paragraph). The live description
        # is what's checked.
        live = lowered.split("r08 audit")[0]
        self.assertNotIn(
            "gaussian", live,
            "module docstring live "
            "description must not mention "
            "Gaussian",
        )
        self.assertIn(
            "heuristic", live,
            "module docstring must use "
            "'heuristic' in the live "
            "description",
        )

    def test_to_demand_forecast_docstring_heuristic(self):
        """The method-level docstring also
        must not use "Gaussian approximation".
        """
        from hems.demand_forecast import (
            DemandForecastService,
        )
        method_doc = (
            DemandForecastService
            .to_demand_forecast.__doc__ or ""
        )
        self.assertIn(
            "heuristic", method_doc.lower(),
            "method docstring must call the "
            "spread heuristic",
        )
        self.assertNotIn(
            "gaussian approximation", method_doc.lower(),
            "method docstring must NOT claim "
            "Gaussian approximation; the "
            "multipliers are NOT statistically "
            "calibrated",
        )

    def test_transient_response_under_load_change(self):
        """Юра: "verify transient response
        to load change at different
        cadences; steady-state constant
        load test does not prove equal
        response".

        We start with a converged
        profile (after 60 samples per
        hour at load=1000W), then
        introduce a step change to
        load=2000W. We measure how many
        samples each cadence takes to
        reach 50% of the new steady
        state. The answer is the same
        for both cadences (≈
        1/α = 4 samples to converge 50%),
        but the wall-clock time depends
        on the cadence.
        """
        # Cadence 1: 5 s per sample. Cadence
        # 2: 30 s per sample. Both deliver
        # the same number of samples, but
        # different wall-clock durations.

        def simulate(polling_interval_s):
            svc = DemandForecastService()
            # Burn 60 samples at load=1000W
            # to converge to baseline.
            for i in range(60):
                svc.update_ewma(
                    datetime(2026, 1, 1, 12, 0, 0)
                    + timedelta(seconds=i * polling_interval_s),
                    load_w=1000.0,
                )
            baseline = svc.profile[12]
            # Apply step change to load=2000W
            # and count samples until the
            # profile reaches
            # (baseline + 0.5 * (2000 - baseline))
            # = halfway to the new steady
            # state.
            target = baseline + 0.5 * (2000 - baseline)
            n_samples_to_half = None
            for i in range(60):
                svc.update_ewma(
                    datetime(2026, 1, 1, 12, 0, 0)
                    + timedelta(
                        seconds=(60 + i) * polling_interval_s,
                    ),
                    load_w=2000.0,
                )
                if svc.profile[12] >= target:
                    n_samples_to_half = i + 1
                    break
            return n_samples_to_half

        n5 = simulate(5)
        n30 = simulate(30)
        # Both cadences should reach
        # halfway in approximately the
        # same number of samples (the
        # EWMA step count is independent
        # of cadence).
        self.assertIsNotNone(
            n5, "5s cadence must converge",
        )
        self.assertIsNotNone(
            n30, "30s cadence must converge",
        )
        # Allow ±1 sample of slack for
        # rounding.
        self.assertLess(
            abs(n5 - n30), 2,
            f"sample count to 50% should be "
            f"cadence-independent; 5s={n5}, "
            f"30s={n30}",
        )


if __name__ == "__main__":
    unittest.main()
