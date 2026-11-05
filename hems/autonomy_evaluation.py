"""Read-only assessment of backup-duration observations.

A grid restoration only establishes a lower bound; it does not prove the
battery could not have supported the load for longer.
"""
from datetime import datetime
import math


def evaluate_observation(predicted_minutes, start, end, outcome):
    """Evaluate an observed outage, distinguishing exhaustion from censoring."""
    if isinstance(predicted_minutes, bool) or not isinstance(predicted_minutes, (float, int)):
        raise ValueError('prediction must be a number')
    if not math.isfinite(predicted_minutes) or predicted_minutes <= 0:
        raise ValueError('prediction must be finite and positive')
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise ValueError('timestamps must be datetime objects')
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError('timestamps must be aware and ordered')
    if outcome not in ('battery_exhausted', 'grid_restored', 'telemetry_lost'):
        raise ValueError('unsupported outcome')
    duration = (end.timestamp() - start.timestamp()) / 60
    exact = outcome == 'battery_exhausted'
    return {'predicted_minutes': float(predicted_minutes),
            'observed_minutes': round(duration, 2), 'outcome': outcome,
            'observation': 'exact' if exact else ('grid_outage_observed' if outcome == 'grid_restored' else 'incomplete'),
            'absolute_error_minutes': round(abs(duration - predicted_minutes), 2) if exact else None,
            'prediction_below_observed_lower_bound': None}


class AutonomyObservationTracker:
    """Track an outage against a previously supplied, time-stamped forecast.

    Callers must persist ``snapshot`` and restore it on startup. This class
    does not infer exhaustion from a lost connection or a restored grid.
    """
    def __init__(self, state=None):
        state = state or {}
        self.forecast = state.get('forecast')
        self.active = state.get('active')
        self.completed = list(state.get('completed') or [])[-30:]

    def snapshot(self):
        return {'forecast': self.forecast, 'active': self.active,
                'completed': list(self.completed)}

    def offer_forecast(self, predicted_minutes, issued_at):
        if self.active is not None:
            return False
        # Reuse validation and require an unambiguous aware issue time.
        if not isinstance(issued_at, datetime) or issued_at.utcoffset() is None:
            raise ValueError('forecast time must be timezone-aware')
        if (isinstance(predicted_minutes, bool) or not isinstance(predicted_minutes, (float, int))
                or not math.isfinite(predicted_minutes) or predicted_minutes <= 0):
            raise ValueError('prediction must be finite and positive')
        self.forecast = {'predicted_minutes': float(predicted_minutes), 'issued_at': issued_at.isoformat()}
        return True

    def start(self, started_at, max_age_seconds):
        if self.active is not None or self.forecast is None:
            return False
        if not isinstance(started_at, datetime) or started_at.utcoffset() is None:
            raise ValueError('start must be timezone-aware')
        issue = datetime.fromisoformat(self.forecast['issued_at'])
        age = started_at.timestamp() - issue.timestamp()
        if not 0 <= age <= max_age_seconds:
            return False
        self.active = {**self.forecast, 'started_at': started_at.isoformat()}
        self.forecast = None
        return True

    def finish(self, ended_at, outcome):
        if self.active is None:
            return None
        started = datetime.fromisoformat(self.active['started_at'])
        result = evaluate_observation(self.active['predicted_minutes'], started, ended_at, outcome)
        result.update({'issued_at': self.active['issued_at'], 'started_at': self.active['started_at'],
                       'ended_at': ended_at.isoformat()})
        self.completed = (self.completed + [result])[-30:]
        self.active = None
        return result


def estimate_constant_load_minutes(capacity_kwh, soc_pct, reserve_pct, load_w, pv_w):
    """Diagnostic scenario, not a validated runtime or control instruction.

    Assumes current net load persists and 100% energy conversion efficiency.
    Returns None if telemetry is missing, invalid, or net load is too small.
    """
    values = (capacity_kwh, soc_pct, reserve_pct, load_w, pv_w)
    if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in values):
        return None
    if capacity_kwh <= 0 or not 0 <= reserve_pct < soc_pct <= 100:
        return None
    if load_w < 0 or pv_w < 0 or load_w > 60000 or pv_w > 60000:
        return None
    net_load = load_w - pv_w
    if net_load < 50:
        return None
    minutes = capacity_kwh * ((soc_pct - reserve_pct) / 100) * 1000 / net_load * 60
    return {'minutes': round(minutes, 1), 'net_load_w': round(net_load, 1),
            'basis': 'constant_current_net_load_ideal_conversion',
            'warning': 'not_validated_excludes_losses_and_future_changes'}


class AutonomyMonitor:
    """Observation-only recorder; a grid outage is not proof of battery runtime."""
    def __init__(self, state=None):
        state = state if isinstance(state, dict) else {}
        self.tracker = AutonomyObservationTracker(state.get('tracker'))
        self.last_grid = state.get('last_grid') if type(state.get('last_grid')) is bool else None
        self.last_seen = state.get('last_seen')
        self.current_estimate = None

    def snapshot(self):
        return {'tracker': self.tracker.snapshot(), 'last_grid': self.last_grid,
                'last_seen': self.last_seen}

    def observe(self, now, grid, *, capacity, soc, reserve, load, pv, max_gap_seconds):
        if not isinstance(now, datetime) or now.utcoffset() is None or type(grid) is not bool:
            raise ValueError('observation requires aware time and a boolean grid state')
        if not isinstance(max_gap_seconds, (int, float)) or isinstance(max_gap_seconds, bool) or not math.isfinite(max_gap_seconds) or max_gap_seconds <= 0:
            raise ValueError('invalid observation gap')
        prior = None
        try:
            prior = datetime.fromisoformat(self.last_seen) if self.last_seen else None
            if prior is not None and prior.utcoffset() is None:
                prior = None
        except (TypeError, ValueError):
            prior = None
        gap = (now.timestamp() - prior.timestamp()) if prior else None
        continuous = gap is not None and 0 <= gap <= max_gap_seconds
        changed = False
        if self.tracker.active and not continuous:
            # Never treat a missing interval as proof of continuing supply.
            start = datetime.fromisoformat(self.tracker.active['started_at'])
            if prior and prior > start:
                self.tracker.finish(prior, 'telemetry_lost')
            else:
                self.tracker.active = None
            changed = True
        if self.tracker.active and grid and continuous:
            self.tracker.finish(now, 'grid_restored')
            changed = True
        if not grid and self.last_grid is True and continuous:
            changed = self.tracker.start(now, max_gap_seconds) or changed
        self.current_estimate = None
        if grid:
            self.current_estimate = estimate_constant_load_minutes(capacity, soc, reserve, load, pv)
            if self.current_estimate:
                self.tracker.offer_forecast(self.current_estimate['minutes'], now)
            else:
                self.tracker.forecast = None
        self.last_grid = grid
        self.last_seen = now.isoformat()
        return changed

    def status(self):
        return {'current_estimate': self.current_estimate,
                'active': self.tracker.active,
                'recent': self.tracker.completed[-7:],
                'quality': 'grid_observation_not_confirmed_backup_runtime',
                'source': 'cloud_polled', 'exact_runtime_supported': False}
