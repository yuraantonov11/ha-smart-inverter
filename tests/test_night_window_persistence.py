"""Exercise the actual recommendation persistence after a fresh plan."""
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from hems.pv_coordinator import PvLearningCoordinatorMixin
from pv_test_support import Checks

check = Checks()
c = PvLearningCoordinatorMixin.__new__(PvLearningCoordinatorMixin)
c._entry = SimpleNamespace(options={'predictive_mode': 'shadow', 'reserve_soc': 20})
now = datetime(2026, 10, 3, 12)
hint = SimpleNamespace(night_charge_start_hour=23, night_charge_end_hour=7)
plan = SimpleNamespace(generated_at=now)
c._hems = SimpleNamespace(_last_predictive_hint=hint, _last_predictive_plan=plan)
def update(entry, options):
    entry.options = options
c.hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=Mock(side_effect=update)))
c._persist_night_recommendation(now)
check(c._entry.options['night_charge_window_recommended'] == {'start_hour': 23, 'end_hour': 7}, '23-07 recommendation persisted')
check(c._entry.options['predictive_mode'] == 'shadow' and c._entry.options['reserve_soc'] == 20, 'mode and safety options preserved')
plan.generated_at = now+timedelta(minutes=5)
c._persist_night_recommendation(plan.generated_at)
check(c.hass.config_entries.async_update_entry.call_count == 1, 'same window not persisted again')
hint.night_charge_start_hour = 1
c._persist_night_recommendation(plan.generated_at)
check(c.hass.config_entries.async_update_entry.call_count == 1, 'changed window rate limited')
plan.generated_at = now+timedelta(hours=1)
c._persist_night_recommendation(plan.generated_at)
check(c.hass.config_entries.async_update_entry.call_count == 2, 'changed window saved after one hour')
for start, end in ((-1, -1), (24, 7), (True, 7), (7, 7)):
    hint.night_charge_start_hour, hint.night_charge_end_hour = start, end
    plan.generated_at += timedelta(hours=1)
    c._persist_night_recommendation(plan.generated_at)
check(c.hass.config_entries.async_update_entry.call_count == 2, 'skip and invalid windows rejected')
hint.night_charge_start_hour, hint.night_charge_end_hour = 23, 7
c._persist_night_recommendation(plan.generated_at+timedelta(seconds=5))
check(c.hass.config_entries.async_update_entry.call_count == 2, 'stale plan rejected')
last = c._night_window_last_persist_at
c.hass.config_entries.async_update_entry.side_effect = RuntimeError('write failed')
c._persist_night_recommendation(plan.generated_at)
check(c._night_window_last_persist_at == last, 'failed update leaves retry timestamp unchanged')
c.hass.config_entries.async_update_entry.side_effect = update
c._persist_night_recommendation(plan.generated_at)
check(c._entry.options['night_charge_window_recommended']['start_hour'] == 23, 'failed update retried')
check.finish()
