"""Use tomorrow's actual forecast and elapsed hours, without changing guards."""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from hems.telemetry import build_planner_inputs
from hems.predictive import PredictiveHemsController
from hems.engine import HemsEngine
from hems.pv_learning import PvLearningState
from hems.pv_coordinator import PvLearningCoordinatorMixin
from pv_test_support import Checks, kyiv_2026

check = Checks()
tz = kyiv_2026()

def inputs(now):
    first = int(now.replace(minute=0, second=0, microsecond=0).timestamp())
    dated = {first+i*3600: float(100+i*10) for i in range(24)}
    return build_planner_inputs(raw={'gridVoltage': 230, 'batterySoc': 60, 'pvPower': 0,
        'loadPower': 300, 'gridPower': 0, 'batteryPower': 0}, now=now,
        forecast_today_kwh=3., forecast_tomorrow_kwh=6., hourly_pv=[50.]*24,
        hourly_radiation=[100.]*24, hourly_weather_codes=[0]*24,
        dated_hourly_pv=dated, tariff_schedule=[4.32]*24,
        consumption_history=[[300.]*24]*7, battery_capacity_kwh=4.8)

now = datetime(2026, 10, 3, 20, 30, tzinfo=tz)
pi = inputs(now)
controller = PredictiveHemsController()
_, plan = controller.decide(pi)
check(plan.hourly[4].pv_w == 140. and plan.hourly[4].timestamp.date().isoformat() == '2026-10-04', 'after midnight uses dated tomorrow value, not today 50 W')
check(plan.hourly[0].timestamp.minute == 30 and plan.hourly[0].pv_w == 100., 'current partial hour maps to its hourly bucket')
for date, repeat in ((datetime(2026,10,25,2,30,tzinfo=tz), True), (datetime(2026,3,29,2,30,tzinfo=tz), False)):
    pi_dst = inputs(date)
    _, p = controller.decide(pi_dst)
    timestamps = [h.timestamp.timestamp() for h in p.hourly]
    check(len(set(timestamps)) == 24 and all(b-a == 3600 for a,b in zip(timestamps,timestamps[1:])), 'DST plans 24 elapsed hours without missing/duplicate instants')
    local_hours = [h.hour for h in p.hourly]
    check(local_hours.count(3) == (2 if repeat else 0), 'DST repeats or skips local hour correctly')
    check([h.pv_w for h in p.hourly] == [100.+i*10 for i in range(24)], 'DST PV stays bound to UTC timestamps')
broken = inputs(now)
del broken.dated_hourly_pv[next(iter(broken.dated_hourly_pv))+8*3600]
try:
    controller.decide(broken)
    check(False, 'dated gap must not become zero or today fallback')
except ValueError:
    check(True, 'dated gap must not become zero or today fallback')
check(controller.last_decision is None, 'failed plan cannot retain actionable recommendation')

c = PvLearningCoordinatorMixin.__new__(PvLearningCoordinatorMixin)
c._pv_local_now = lambda: now
c._pv_learning = PvLearningState('Europe/Kyiv', 50., 30.)
c._pv_calibrator = c._pv_learning.calibrator
c._forecast = None
c._raw_forecast_kwh = {'2026-10-03':5., '2026-10-04':5., '2026-10-05':8.}
c._raw_hourly_forecast = []
for day, energy in c._raw_forecast_kwh.items():
    for h in range(24):
        dt = datetime.fromisoformat(day).replace(hour=h,tzinfo=tz)
        c._raw_hourly_forecast.append({'time':dt.isoformat(),'timestamp':int(dt.timestamp()),'power_w':energy*1000 if h==16 else 0.})
c._forecast_today_kwh = c.forecast_tomorrow_kwh = 5.
c.forecast_day_after_kwh = 8.
for _ in range(4):
    c._pv_calibrator.record(5.,4.)
c._adjust_daily_forecasts()
check(c._forecast_today_kwh == 4. and sum(c.hourly_forecast_today)/1000 == 4., 'today graph and daily total share calibrated energy')
for day, expected in (('2026-10-04',4.),('2026-10-05',7.)):
    values = [v for t,v in c._dated_hourly_pv_forecast.items() if datetime.fromtimestamp(t,tz).date().isoformat()==day]
    check(sum(values)/1000 == expected, 'future dated hours agree with corrected daily total')
check(c.hourly_forecast_today[0] == 0., 'daily correction cannot invent night generation')
before = dict(c._dated_hourly_pv_forecast)
c._adjust_daily_forecasts()
check(c._dated_hourly_pv_forecast == before, 'repeated correction does not compound')
check(c._raw_forecast_kwh['2026-10-04']==5. and c._raw_hourly_forecast[16]['power_w']==5000., 'raw issued energy and powers remain unchanged')
c._raw_hourly_forecast[16]['power_w'] = 5004.
c._adjust_daily_forecasts()
check(abs(sum(c.hourly_forecast_today)/1000-c._forecast_today_kwh) < .000001, 'rounded source daily sum still agrees with hourly curve')
c._pv_calibrator.reset()
for _ in range(4):
    c._pv_calibrator.record(19.9,25.)
c._raw_forecast_kwh['2026-10-03'] = 19.9
c._raw_hourly_forecast[16]['power_w'] = 19900.
c._adjust_daily_forecasts()
check(c._forecast_today_kwh == 20. and max(c.hourly_forecast_today) == 20000., 'positive correction preserves existing twenty kW ceiling and total')

engine = HemsEngine()
engine._predictive_enabled = True
engine._hourly_pv_forecast = pi.hourly_pv
engine._hourly_radiation = pi.hourly_radiation
engine._hourly_weather_codes = pi.hourly_weather_codes
engine._dated_hourly_pv_forecast = pi.dated_hourly_pv
engine._planner_forecast_now = now
engine_inputs = {'grid_voltage':230., 'soc':60., 'pv_power':0.,'load_power':300.,
                'grid_power':0.,'battery_power':0.,'smart_mode':0,'grid_available':True,
                'forecast_today_kwh':3.,'forecast_tomorrow_kwh':6.}
hint, actual_plan = engine._evaluate_predictive(now.replace(tzinfo=None),engine_inputs)
check(actual_plan is not None and actual_plan.hourly[4].pv_w == 140., 'real engine wiring carries dated tomorrow forecast')
engine._dated_hourly_pv_forecast = broken.dated_hourly_pv
check(engine._evaluate_predictive(now.replace(tzinfo=None),engine_inputs) == (None,None), 'engine rejects incomplete next 24 hours before hint')
engine._dated_hourly_pv_forecast = {}
check(engine._evaluate_predictive(now.replace(tzinfo=None),engine_inputs) == (None,None), 'explicit missing dated forecast never falls back to repeated day')
check.finish()
