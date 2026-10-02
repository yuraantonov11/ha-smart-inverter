"""Option -> controller -> real telemetry inputs; recommendations stay read-only."""
import os
import sys
import ast
import asyncio
from datetime import datetime,timedelta,timezone
from types import ModuleType,SimpleNamespace
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,os.path.join(os.path.dirname(__file__),'..'))
from hems.pv_coordinator import PvLearningCoordinatorMixin
from hems.predictive import PredictiveHemsController,normalize_night_window,plan_night_charge
from hems.telemetry import build_planner_inputs
from pv_test_support import Checks

_check = Checks()
def coordinator(options):
    c = PvLearningCoordinatorMixin.__new__(PvLearningCoordinatorMixin)
    c._entry = SimpleNamespace(entry_id='test',options=options)
    c._hems = SimpleNamespace(_predictive_mode='shadow')
    def update(entry,options):
        entry.options = options
    c.hass = SimpleNamespace(config=SimpleNamespace(time_zone='UTC'),
                            config_entries=SimpleNamespace(async_update_entry=Mock(side_effect=update)))
    with patch('hems.pv_coordinator.ZoneInfo',return_value=timezone.utc):
        c._init_pv_learning()
    return c

now = datetime(2026,10,2,20)
def inputs(forecast=0,soc=20):
    return build_planner_inputs({'batterySoc':soc,'gridVoltage':230},now=now,
        forecast_tomorrow_kwh=forecast,forecast_today_kwh=2,
        hourly_pv=[100.]*24,hourly_radiation=[100.]*24,hourly_weather_codes=[0]*24,
        tariff_schedule=[4.32]*24,consumption_history=[[300.]*24]*30,battery_capacity_kwh=12)
c = coordinator({})
_check((c.night_charge_start_hour,c.night_charge_end_hour) == (23,7),'default options')
c = coordinator({'predictive_mode':'shadow','night_charge_start_hour':21,'night_charge_end_hour':6})
pi = inputs()
controller = c._hems._predictive_controller
hint = controller.suggest(pi)
_check(pi.night_charge_window == (21,6),'configured window reaches real telemetry inputs without engine edits')
_check((hint.night_charge_start_hour,hint.night_charge_end_hour) == (21,6),'full recommendation uses configured window')
_check(plan_night_charge(inputs(4,20),80)[:2] == (5,7),'default late two-hour charge')
custom = inputs(4,20)
custom.night_charge_window = (21,6)
_check(plan_night_charge(custom,80)[:2] == (4,6),'configured late two-hour charge')
custom.forecast_tomorrow_kwh = 2
_check(plan_night_charge(custom,80)[:2] == (2,6),'configured late four-hour charge')
custom.night_charge_window = (5,6)
_check(plan_night_charge(custom,80)[:2] == (5,6),'partial window clipped to permitted duration')
_check(plan_night_charge(inputs(4,60),80)[:2] == (-1,-1),'skip charge preserved')
_check(all(normalize_night_window(w) == (23,7) for w in [(1,1),(-1,7),(23,24),('23',7),(True,7),None]),'invalid windows fallback')
decision,plan = controller.decide(pi)
_check(all(p.charger != 'SNU' for p in plan.hourly if 6 <= p.hour < 21),'rollout never grid-charges outside configured night')
_check(all(p.tariff == 4.32 for p in plan.hourly),'night window does not rewrite tariff schedule')
c._hems._last_predictive_hint,c._hems._last_predictive_plan = hint,plan
c._persist_night_recommendation(now)
_check(c._entry.options['night_charge_window_recommended'] == {'start_hour':21,'end_hour':6},'successful decide persists recommendation')
c._persist_night_recommendation(now+timedelta(seconds=5))
_check(c.hass.config_entries.async_update_entry.call_count == 1,'unchanged/stale recommendation never rewrites options')
_check(c._entry.options['predictive_mode'] == 'shadow','recommendation leaves Shadow unchanged')
hint.night_charge_start_hour = 23
plan.generated_at = now+timedelta(minutes=10)
c._persist_night_recommendation(plan.generated_at)
_check(c.hass.config_entries.async_update_entry.call_count == 1,'changed recommendation throttled to hour')
plan.generated_at = now+timedelta(hours=1)
c._persist_night_recommendation(plan.generated_at)
_check(c.hass.config_entries.async_update_entry.call_count == 2,'changed recommendation saved after hour')
_check(not c.check_assist_ready(),'empty calibrator blocks readiness')
for _ in range(4):
    c._pv_calibrator.record(4,4)
_check(c.check_assist_ready(),'four accurate real pairs ready')
_check(c._entry.options['predictive_mode'] == 'shadow' and c._hems._predictive_mode == 'shadow','readiness never enables Assist')
c.api = SimpleNamespace(device_sn='device')
registry = SimpleNamespace(async_get_entity_id=Mock(return_value='sensor.renamed_pv'),
                           async_get=Mock(return_value=SimpleNamespace(config_entry_id='other')))
helpers = ModuleType('homeassistant.helpers')
helpers.entity_registry = SimpleNamespace(async_get=lambda hass:registry)
with patch.dict(sys.modules,{'homeassistant.helpers':helpers}):
    _check(c._history_entity('pv_power','sensor.legacy') == 'sensor.renamed_pv','renamed sensor resolved by device identity')
    registry.async_get_entity_id.return_value = None
    try:
        c._history_entity('pv_power','sensor.legacy')
        _check(False,'other inverter history rejected')
    except LookupError:
        _check(True,'other inverter history rejected')
    registry.async_get.return_value = SimpleNamespace(config_entry_id=c._entry.entry_id)
    _check(c._history_entity('pv_power','sensor.legacy') == 'sensor.legacy','legacy sensor accepted only for owning entry')
# Exercise the exact registered service handler with multiple config entries.
tree = ast.parse((Path(__file__).resolve().parents[1]/'services/__init__.py').read_text(encoding='utf-8'))
register = next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name == 'async_register_services')
handler = next(n for n in register.body if isinstance(n,ast.AsyncFunctionDef) and n.name == 'handle_auto_check_assist')
ready_one,ready_two = Mock(),Mock()
entries = [SimpleNamespace(entry_id='one'),SimpleNamespace(entry_id='two')]
hass = SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda domain:entries),
    data={'powmr_inverter':{'one':{'coordinator':SimpleNamespace(check_assist_ready=ready_one)},
                          'two':{'coordinator':SimpleNamespace(check_assist_ready=ready_two)}}})
ns = {'hass':hass,'DOMAIN':'powmr_inverter','ServiceCall':object}
exec(compile(ast.Module(body=[handler],type_ignores=[]),'services/__init__.py','exec'),ns)
asyncio.run(ns['handle_auto_check_assist'](SimpleNamespace(data={})))
_check(ready_one.call_count == ready_two.call_count == 1,'service checks every loaded inverter')
asyncio.run(ns['handle_auto_check_assist'](SimpleNamespace(data={'entry_id':'one'})))
_check(ready_one.call_count == 2 and ready_two.call_count == 1,'service selects explicit config entry')
try:
    asyncio.run(ns['handle_auto_check_assist'](SimpleNamespace(data={'entry_id':'missing'})))
    _check(False,'unknown service entry rejected')
except ValueError:
    _check(True,'unknown service entry rejected')
_check.finish()
