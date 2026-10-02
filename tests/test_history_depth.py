"""Thirty-day history; complete PV hours and HA cumulative energy at DST."""
import os
import sys
from datetime import datetime, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from hems.history_builder import build_hourly_load_matrix
from hems.pv_learning import complete_hourly_days, daily_energy_deltas, day_bounds
from hems.pv_coordinator import PvLearningCoordinatorMixin
from hems.telemetry import build_planner_inputs
from pv_test_support import Checks, kyiv_2026

_check = Checks()
now = datetime(2026,10,2)
def samples(days):
    return [(now-timedelta(days=d)+timedelta(hours=h),300.)
            for d in range(days,0,-1) for h in range(24)]
_check(len(build_hourly_load_matrix(samples(30),now,days=30)) == 30,"thirty valid days")
_check(len(build_hourly_load_matrix(samples(5),now,days=30)) == 5,"five available days")
coord = PvLearningCoordinatorMixin.__new__(PvLearningCoordinatorMixin)
_check(coord._load_matrix_days == coord._pv_matrix_days == 30,"coordinator default depths")
_check(len(build_planner_inputs({},consumption_history=[[300.]*24]*40).consumption_history) == 30,"telemetry passes thirty latest days")
tz = kyiv_2026()
for day,count in [('2026-03-29',23),('2026-10-25',25),('2026-10-02',24)]:
    start,end = day_bounds(day,tz)
    rows = [{'start':(start+timedelta(hours=h)).timestamp(),'mean':1000.} for h in range(count)]
    today = datetime.fromisoformat(day).date()+timedelta(days=1)
    _check(complete_hourly_days(rows,tz,today).get(day) == count,f"{count}-hour local day integrates elapsed hours")
    _check(day not in complete_hourly_days(rows[:-1],tz,today),"missing PV hour invalidates day")
    sums = [{'start':(start+timedelta(hours=h-1)).timestamp(),'sum':10000+h*1000} for h in range(count+1)]
    _check(daily_energy_deltas(sums,'Wh',tz,today).get(day) == count,"cumulative Wh becomes daily kWh delta")
    _check(not daily_energy_deltas(sums,None,tz,today),"unknown energy unit rejected")
    _check(not daily_energy_deltas(sums[1:],'Wh',tz,today),"missing midnight endpoint rejected")
    sums[4]['sum'] = 0
    _check(not daily_energy_deltas(sums,'Wh',tz,today),"decreasing cumulative sum rejected")
    rows[3]['mean'] = float('nan')
    _check(not complete_hourly_days(rows,tz,today),"NaN never fills a PV gap")
start,_ = day_bounds('2026-10-02',tz)
zeros = [{'start':(start+timedelta(hours=h)).timestamp()*1000,'mean':0} for h in range(24)]
_check(complete_hourly_days(zeros,tz,datetime(2026,10,3).date())['2026-10-02'] == 0,"complete zero day and millisecond timestamps")
_check.finish()
