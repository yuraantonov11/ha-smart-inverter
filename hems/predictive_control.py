"""Opt-in predictive control using the accepted engine's safety pipeline.

This extension selects a proposal BEFORE the engine filters writes. It never
writes the inverter API, changes the selected smart mode or enables Assist.
"""
from __future__ import annotations

import logging
import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .engine import HemsEngine, SmartMode, OutputPriority, ChargerPriority
from .engine import _normalize_output, _normalize_charger

_LOGGER = logging.getLogger(__name__)

DEFAULT_OPTIONS = {
    "predictive_default_mode": "Shadow",
    "predictive_night_window_start_hour": 23,
    "predictive_night_window_end_hour": 7,
    "predictive_min_confidence_for_assist": 0.2,
}


class _ZeroCalibrationMetrics:
    """Sentinel for the
    ``_read_calibrator_metrics_safe``
    fallback. Mirrors the
    ``CalibrationMetrics``
    dataclass so attribute
    access matches the real
    type. Audit T22 round 9
    (R9.1) — must be defined
    at module level so the
    engine's ``evaluate`` init
    can fall back to
    ``samples=0,
    confidence_factor=0.0``
    when the calibrator is not
    yet wired (coordinator
    startup, predictive assist
    disabled)."""
    sample_count = 0
    confidence_factor = 0.0


def parse_predictive_options(options):
    """Validate UI options without silently coercing invalid values."""
    result = {key: options.get(key, value) for key, value in DEFAULT_OPTIONS.items()}
    if result["predictive_default_mode"] not in ("Off", "Shadow", "Assist"):
        raise ValueError("invalid predictive default mode")
    for key in ("predictive_night_window_start_hour", "predictive_night_window_end_hour"):
        if type(result[key]) is not int or not 0 <= result[key] <= 23:
            raise ValueError("invalid predictive night hour")
    if result["predictive_night_window_start_hour"] == result["predictive_night_window_end_hour"]:
        raise ValueError("empty predictive night window")
    value = result["predictive_min_confidence_for_assist"]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("invalid predictive confidence")
    return result


def apply_feedback(engine, action, now, duration_min=30, new_target_soc=None):
    """Return serializable feedback; approve has no state-changing effect."""
    if action not in ("approve", "reject", "modify"):
        raise ValueError("unsupported feedback action")
    if type(duration_min) is not int or not 1 <= duration_min <= 1440:
        raise ValueError("duration_min must be 1..1440")
    if action == "modify" and (type(new_target_soc) is not int or not 20 <= new_target_soc <= 100):
        raise ValueError("modify requires new_target_soc 20..100")
    if action == "approve":
        return None
    until = now + timedelta(minutes=duration_min)
    # Feedback may extend, never shorten an existing physical manual hold.
    if engine._manual_override_until and engine._manual_override_until > until:
        until = engine._manual_override_until
    engine._manual_override_until = until
    engine._predictive_user_target_soc = new_target_soc if action == "modify" else None
    return {"action": action, "target_soc": engine._predictive_user_target_soc,
            "until": until.astimezone(timezone.utc).isoformat()}


def restore_feedback(engine, record, now):
    """Restore only a valid, unexpired hold in the evaluation clock's zone."""
    try:
        until = datetime.fromisoformat(record["until"])
        if until.tzinfo is None:
            return
        until = until.astimezone(now.tzinfo) if now.tzinfo else until.astimezone().replace(tzinfo=None)
        target = record.get("target_soc")
        if record.get("action") not in ("reject", "modify") or until <= now:
            return
        if record["action"] == "modify" and (type(target) is not int or not 20 <= target <= 100):
            return
        engine._manual_override_until = until
        engine._predictive_user_target_soc = target
    except (TypeError, ValueError, KeyError):
        return


class PredictiveControlEngine(HemsEngine):
    """Add calibrated proposals without changing engine.py or its guards."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.predictive_min_confidence = 0.2
        self.predictive_storm_allowed = False
        self._last_predictive_decision = None
        self._predictive_user_target_soc = None
        self.predictive_decision_state = {}
        self._predictive_selected = False
        self._predictive_log_signature = None

    def evaluate(self, **kwargs):
        self._last_predictive_decision = None
        self._predictive_selected = False
        self._predictive_ready = False
        now = kwargs.get("now") or datetime.now()
        if not self._manual_override_until or self._manual_override_until <= now:
            self._predictive_user_target_soc = None
        mode = self.predictive_tuning.predictive_mode
        # Audit T22 round 9 (R9.1): the
        # init dict MUST publish
        # ``readiness``,
        # ``real_pairs``, and
        # ``model_quality`` from the
        # very first evaluate cycle.
        # Earlier rounds left these
        # keys missing whenever the
        # hint-proposal path did not
        # run (manual_override_hold,
        # hems_auto_off,
        # inverter_offline) and the
        # AI view rows resolved to
        # ``None``. The calibrator is
        # independent of the engine's
        # decision pipeline, so its
        # metrics are valid even when
        # the engine skips planning.
        metrics = self._read_calibrator_metrics_safe()
        self.predictive_decision_state = {
            "mode": mode.title(), "output_priority": None, "charger_priority": None,
            "target_soc": self._predictive_user_target_soc, "reason": "planner_unavailable",
            "confidence": 0.0, "samples": 0, "applied": False,
            "real_pairs": metrics.sample_count,
            "model_quality": metrics.confidence_factor,
            "readiness": False,
            "override_pending_until": self._manual_override_until.isoformat() if self._manual_override_until and self._manual_override_until > now else None,
        }
        decision = super().evaluate(**kwargs)
        self.predictive_decision_state["execution_reason"] = decision.reason
        # Audit T22 round 9 (R9.1):
        # even if the planner
        # returned ``(None, None)``
        # because of hold/off, the
        # accumulated calibrator
        # samples and the
        # confidence_factor are
        # still meaningful and must
        # remain visible. Re-publish
        # them here so the sensor
        # always exposes the
        # cumulative learning
        # signal, not just the
        # latest decision attempt.
        samples_now = int(metrics.sample_count)
        model_quality_now = float(metrics.confidence_factor)
        try:
            confidence_now = float(
                getattr(self._last_predictive_hint, "confidence", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            confidence_now = 0.0
        if not math.isfinite(confidence_now):
            confidence_now = 0.0
        if not math.isfinite(model_quality_now):
            model_quality_now = 0.0
        self.predictive_decision_state["samples"] = samples_now
        self.predictive_decision_state["real_pairs"] = samples_now
        self.predictive_decision_state["model_quality"] = model_quality_now
        self.predictive_decision_state["confidence"] = confidence_now
        return decision

    def _read_calibrator_metrics_safe(self):
        """Read calibrator metrics, falling back to zero when unavailable.

        Audit T22 round 9 (R9.1):
        ``self._predictive_controller``
        may be unbound during
        coordinator startup or when
        predictive assist is
        disabled. Treat both as
        ``samples=0,
        confidence_factor=0.0``
        rather than raising.
        """
        try:
            controller = getattr(self, "_predictive_controller", None)
            if controller is None:
                return _ZeroCalibrationMetrics()
            calibrator = getattr(controller, "calibrator", None)
            if calibrator is None:
                return _ZeroCalibrationMetrics()
            return calibrator.metrics()
        except (AttributeError, TypeError, ValueError):
            return _ZeroCalibrationMetrics()

    def _evaluate_predictive(self, now, inputs):
        hint, plan = super()._evaluate_predictive(now, inputs)
        controller = getattr(self, "_predictive_controller", None)
        proposal = getattr(controller, "last_decision", None) if plan is not None else None
        self._last_predictive_decision = proposal
        # Audit T22 round 7 (R7.1):
        # the engine is the single
        # source of truth for
        # ``readiness``,
        # ``real_pairs``, and
        # ``model_quality``. The
        # ``PredictiveDecisionStateSensor``
        # reads
        # ``extra_state_attributes``
        # from
        # ``predictive_decision_state``
        # so we MUST publish these
        # keys on every evaluate
        # cycle. Without this the
        # AI view's ``attribute``
        # rows would resolve to
        # missing keys and the
        # HA frontend would show
        # ``undefined``.
        confidence = 0.0
        samples = 0
        model_quality = 0.0
        self._predictive_ready = False
        if hint is None or proposal is None:
            self.predictive_decision_state.update(
                output_priority=None,
                charger_priority=None,
                target_soc=None,
                reason="no_hint",
                confidence=0.0,
                samples=0,
                real_pairs=0,
                model_quality=0.0,
                readiness=False,
            )
            return None, plan
        try:
            metrics = controller.calibrator.metrics()
            confidence = float(hint.confidence)
            samples = int(metrics.sample_count)
            model_quality = float(metrics.confidence_factor)
            self._predictive_ready = (
                math.isfinite(confidence)
                and confidence >= max(
                    0.2, self.predictive_min_confidence
                )
                and samples >= 3
            )
        except (AttributeError, TypeError, ValueError):
            confidence, samples = 0.0, 0
            model_quality = 0.0
        self.predictive_decision_state.update(
            output_priority={"0": "USB", "2": "SBU"}.get(proposal.output_priority),
            charger_priority={"1": "SNU", "2": "OSO"}.get(proposal.charger_priority),
            target_soc=hint.target_soc_morning,
            reason=proposal.reason,
            confidence=confidence if math.isfinite(confidence) else 0.0,
            samples=samples,
            # Audit T22 round 7 (R7.1):
            # publish the engine
            # gate so the sensor
            # surfaces it. The AI
            # view's ``attribute``
            # rows resolve directly
            # to these keys.
            real_pairs=samples,
            model_quality=model_quality,
            readiness=self._predictive_ready,
        )
        # Compute a true baseline without legacy hint side effects; the fresh
        # hint remains available for UI and the gated proposal below.
        return None, plan

    def invalidate_predictive(self, reason):
        """No stale applied state survives coordinator-level early returns."""
        self._predictive_selected = False
        self._predictive_ready = False
        self._last_predictive_decision = None
        self._last_predictive_hint = None
        self._last_predictive_plan = None
        self._last_predictive_inputs = None
        self.predictive_decision_state.update(mode=self.predictive_tuning.predictive_mode.title(),
                                             output_priority=None, charger_priority=None,
                                             confidence=0.0, samples=0, applied=False,
                                             real_pairs=0, model_quality=0.0, readiness=False,
                                             reason=reason, execution_reason=reason)

    def _finalize_decision(self, decision, now, inputs, entry_id=None):
        proposal = self._last_predictive_decision
        floor = max(inputs["reserve_soc"] + 2, inputs["min_operating_soc"])
        eligible = (
            self._predictive_mode == "assist" and self._predictive_ready
            and inputs["smart_mode"] in (SmartMode.ADAPTIVE, SmartMode.ARBITRAGE)
            and inputs["soc"] > floor and inputs["grid_available"]
            and not self.keepalive.in_progress and not decision.skip
            and decision.reason not in ("hysteresis_recovery", "evening_reserve_protection",
                                        "emergency_low_soc_daytime", "reserve_soc_protection")
            and proposal is not None and not proposal.skip
            and proposal.output_priority in (OutputPriority.USB, OutputPriority.SBU)
            and proposal.charger_priority in (ChargerPriority.SNU, ChargerPriority.OSO)
        )
        # A calibrated PV shortfall is a recommendation, not proof of a storm.
        if eligible and getattr(self._last_predictive_hint, "storm_preemption", False) and not self.predictive_storm_allowed:
            eligible = False
        if eligible:
            self._predictive_selected = True
            decision = replace(proposal, buzzer_off=decision.buzzer_off)
        result = super()._finalize_decision(decision, now, inputs, entry_id)
        if eligible:
            matches_output = (_normalize_output(inputs["current_output"]) == proposal.output_priority or result.output_priority == proposal.output_priority)
            matches_charger = (_normalize_charger(inputs["current_charger"]) == proposal.charger_priority or result.charger_priority == proposal.charger_priority)
            self._predictive_can_apply = matches_output and matches_charger and not result.skip
            # No writes necessary: current observed priorities already match.
            if result.output_priority is None and result.charger_priority is None:
                self.confirm_predictive_delivery(True)
        return result

    def confirm_predictive_delivery(self, success):
        applied = bool(success and self._predictive_selected and self._predictive_can_apply)
        self.predictive_decision_state["applied"] = applied
        state = self.predictive_decision_state
        signature = (applied, state.get("mode"), state.get("reason"), state.get("output_priority"), state.get("charger_priority"))
        if applied and signature != self._predictive_log_signature:
            _LOGGER.info("Predictive applied: mode=Assist conf=%.2f n=%d applied=%s charger=%s reason=%s",
                         state["confidence"], state["samples"], state["output_priority"], state["charger_priority"], state["reason"])
        self._predictive_log_signature = signature
