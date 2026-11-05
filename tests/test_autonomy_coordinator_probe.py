import ast
import asyncio
import logging
import math
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from hems.autonomy_evaluation import AutonomyMonitor

source = Path(__file__).resolve().parents[1] / 'coordinator.py'
tree = ast.parse(source.read_text(encoding='utf-8'))
cls = next(x for x in tree.body if isinstance(x, ast.ClassDef) and any(getattr(n, 'name', '') == '_async_observe_autonomy' for n in x.body))
method = next(x for x in cls.body if getattr(x, 'name', '') == '_async_observe_autonomy')
module = ast.Module(body=[method], type_ignores=[])
ns = {'math': math, 'datetime': datetime, 'timezone': timezone, 'compute_reserve_soc': lambda opts: opts['reserve_soc'], '_LOGGER': logging.getLogger(__name__)}
exec(compile(ast.fix_missing_locations(module), str(source), 'exec'), ns)

class Store:
    def __init__(self): self.saved = []
    def async_delay_save(self, callback, delay): self.saved.append(callback())

class WrapperTest(unittest.IsolatedAsyncioTestCase):
    async def test_real_wrapper_records_and_rejects_missing_grid(self):
        store = Store()
        obj = SimpleNamespace(_autonomy_monitor=AutonomyMonitor(), _autonomy_store=store,
            api=SimpleNamespace(last_realtime_outcome='fresh_success'), _battery_capacity_kwh=5.12,
            _entry=SimpleNamespace(options={'reserve_soc':20}), update_interval=timedelta(seconds=60),
            _autonomy_last_saved=None)
        observe = ns['_async_observe_autonomy']
        await observe(obj, {'gridVoltage':230, 'loadPower':600, 'pvPower':100}, 70, True)
        self.assertEqual(len(store.saved),1)
        self.assertEqual(obj._autonomy_monitor.status()['current_estimate']['minutes'],307.2)
        await observe(obj, {'loadPower':600,'pvPower':100}, 70, True)
        self.assertEqual(len(store.saved),1)
        self.assertEqual(obj._autonomy_monitor.last_grid,True)
        obj.api.last_realtime_outcome='cached_fallback'
        await observe(obj, {'gridVoltage':0,'loadPower':600,'pvPower':100},70,False)
        self.assertIsNone(obj._autonomy_monitor.tracker.active)
        obj.api.last_realtime_outcome='fresh_success'
        original = obj._autonomy_monitor.tracker.forecast.copy()
        await observe(obj, {'gridVoltage':0,'loadPower':20000,'pvPower':0},70,True)
        self.assertEqual(obj._autonomy_monitor.tracker.forecast, original)
        obj.api.last_realtime_measurement_at = datetime.now(timezone.utc) - timedelta(days=1)
        await observe(obj, {'gridVoltage':230,'loadPower':20000,'pvPower':0},70,True)
        self.assertEqual(obj._autonomy_monitor.tracker.forecast, original)

if __name__ == '__main__': unittest.main()
