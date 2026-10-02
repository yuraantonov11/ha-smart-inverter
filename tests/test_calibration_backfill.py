"""Station training never invents historical day-ahead forecast accuracy."""
import os
import sys
import tempfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
sys.path.insert(0,os.path.join(os.path.dirname(__file__),'..'))
from hems.pv_learning import PvLearningState,train_station
from pv_test_support import Checks

_check = Checks()
start = datetime(2026,6,11)
actual = {(start+timedelta(days=i)).date().isoformat():8. for i in range(113)}
radiation = {day:4. for day in actual}
model = train_station(actual,radiation)
_check(model['gain'] == 2 and model['sample_count'] == 90,"113 historical days train immediately, latest ninety")
validation = model['validation']
_check(validation['train_end'] < validation['test_start'] and validation['mae_kwh'] == 0,"chronological holdout")
_check(validation['train_days'] >= 14 and validation['test_days'] >= 7,"minimum holdout depths")
_check(train_station(dict(list(actual.items())[:6]),radiation) is None,"six days insufficient")
days = dict(list(actual.items())[:30])
changed = dict(days)
for day in list(changed)[-7:]:
    changed[day] = 40.
_check(train_station(changed,radiation)['validation']['mae_kwh'] == 32,"holdout actuals never train their predictor")
_check(train_station({day:0. for day in days},radiation) is None,"all-zero history retains previous model")
state = PvLearningState('UTC',50.45,30.52)
confidence = []
for _ in range(10):
    state.calibrator.record(4,4)
    confidence.append(state.calibrator.metrics().confidence_factor)
_check(all(a < b for a,b in zip(confidence,confidence[1:])),'accurate daily evidence increases forecast confidence')
state.calibrator.reset()
state.calibrator.record(5,0)
_check(state.calibrator.metrics().sample_count == 1 and state.calibrator.metrics().confidence_factor == 0,'zero actual penalizes overforecast')
state.calibrator.record(float('inf'),1)
_check(len(state.calibrator) == 1,'nonfinite inputs rejected')
state.calibrator.metrics()
state.calibrator.load_from_list([[float('nan'),1]])
_check(state.calibrator.metrics().sample_count == 0,'invalid reload cannot reuse cached metrics')
state.model = model
_check(len(state.calibrator) == 0,"archive metrics never seed forecast confidence")
before = datetime(2026,10,1,23,tzinfo=timezone.utc)
after = datetime(2026,10,3,tzinfo=timezone.utc)
_check(state.snapshot('2026-10-02',4,before),"forecast issued before day starts")
_check(not state.snapshot('2026-10-02',99,after),"retrospective overwrite rejected")
_check(state.match({'2026-10-02':2.,'2026-10-01':9.},after) == 1,"date join skips missing forecast")
_check(state.calibrator.metrics().bias_w == -2,"real pair uses kWh")
_check(state.match({'2026-10-02':2},after) == 0,"duplicate refresh rejected")
state.snapshot('2026-10-03',5,before)
_check(state.match({'2026-10-03':0},after+timedelta(days=1)) == 1,"measured zero retained")
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory)/'entry.json'
    legacy = Path(directory)/'pv_fact_pairs.json'
    legacy.write_text('{"samples":[[4000,2000]]}',encoding='utf-8')
    state.save(path)
    fresh = PvLearningState('UTC',50.45,30.52)
    fresh.load(path,legacy)
    _check(fresh.pairs == state.pairs and fresh.model == model,"restore model and dated pairs")
    _check(fresh.match({'2026-10-02':2},after) == 0,"restart deduplication")
    _check(legacy.with_suffix('.json.legacy').exists(),"legacy undated pairs archived")
    _check(len(fresh.calibrator) == 2,"legacy pairs excluded")
    unrelated = PvLearningState('UTC',0,0)
    try:
        unrelated.load(path)
        _check(False,"different station rejected")
    except ValueError:
        _check(len(unrelated.calibrator) == 0,"different station rejected")
    path.write_text('{"version":2,"unit":"Wh"}',encoding='utf-8')
    try:
        fresh.load(path)
        _check(False,"wrong unit rejected")
    except ValueError:
        _check(len(fresh.calibrator) == 2,"invalid reload preserves state")
_check.finish()
