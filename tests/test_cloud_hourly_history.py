"""Historical daily API, real sample validation and bounded cache backfill."""
import ast
import asyncio
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hems.cloud_history import CloudHourlyHistory, measured_pv_hours
from hems.pv_coordinator import PvLearningCoordinatorMixin
from pv_test_support import kyiv_2026


def props(points, key='generationPower', unit='kW'):
    return [{'property': {'key': key, 'unit': unit}, 'timePoints': points}]


def points(day):
    return [{'time': f'{day} {h:02}:00:00', 'value': .1, 'isRealValue': True}
            for h in range(24)] + [
            {'time': f'{day} {h:02}:30:00', 'value': .3, 'isRealValue': True} for h in range(24)]


class CloudHoursTests(unittest.IsolatedAsyncioTestCase):
    day = date(2026, 10, 2)

    def test_conversion_night_and_timezone(self):
        rows = measured_pv_hours(props(points(self.day)), self.day, kyiv_2026())
        self.assertEqual(len(rows), 24)
        self.assertEqual(rows[0]['mean'], 200)
        self.assertEqual(rows[0]['start'], datetime(2026, 10, 1, 21, tzinfo=timezone.utc).timestamp())
        zero = points(self.day)
        for p in zero: p['value'] = 0
        self.assertEqual(measured_pv_hours(props(zero), self.day, timezone.utc)[0]['mean'], 0)

    def test_missing_placeholder_and_duplicates(self):
        p = points(self.day)
        p[24]['isRealValue'] = False
        self.assertEqual(len(measured_pv_hours(props(p), self.day, timezone.utc)), 23)
        p = points(self.day)
        p.append({**p[0], 'value': .9})
        self.assertEqual(len(measured_pv_hours(props(p), self.day, timezone.utc)), 23)
        self.assertEqual(len(measured_pv_hours(props(points(self.day)*2), self.day, timezone.utc)), 24)

    def test_wrong_unit_property_value_and_date(self):
        self.assertEqual(measured_pv_hours(props(points(self.day), unit='kWh'), self.day, timezone.utc), [])
        self.assertEqual(measured_pv_hours(props(points(self.day), key='loadPower'), self.day, timezone.utc), [])
        self.assertEqual(measured_pv_hours(props(points(self.day)), date(2026, 10, 1), timezone.utc), [])
        for bad in (True, -1, None, float('nan'), 21):
            p = points(self.day)
            p[0]['value'] = bad
            self.assertEqual(len(measured_pv_hours(props(p), self.day, timezone.utc)), 23)

    def test_dst_ambiguity_never_guessed(self):
        day = date(2026, 10, 25)
        rows = measured_pv_hours(props(points(day)), day, kyiv_2026())
        self.assertEqual(len(rows), 23)  # Ambiguous 03:00 and 03:30 are omitted.

    async def test_real_api_day_body_and_raw_property_selection(self):
        tree = ast.parse((Path(__file__).resolve().parents[1]/'api.py').read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'InverterApiClient')
        fns = [n for n in cls.body if isinstance(n, ast.AsyncFunctionDef)
               and n.name in ('_fetch_overview', 'fetch_hourly_pv_history_day')]
        for fn in fns:
            for n in ast.walk(fn):
                if isinstance(n, ast.ImportFrom) and n.module == 'hems.cloud_history': n.level = 0
        ns = {'Any': object, 'json': json, '_LOGGER': Mock(), 'SUMMARY_KEY_POWER': 'power',
              'ENDPOINT_OVERVIEW_BASE': '/overview', 'aiohttp': SimpleNamespace(ClientError=RuntimeError)}
        exec(compile(ast.Module(body=fns, type_ignores=[]), 'actual-api', 'exec'), ns)
        body = {}
        class Response:
            status = 200
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def text(self): return json.dumps({'code': 0, 'data': {'properties': props(points(self.day))}})
        response = Response()
        response.day = self.day
        fake = SimpleNamespace(current_station_id='station', _account_device_count=1,
            _apply_rate_limit=AsyncMock(), _build_headers=Mock(return_value={}),
            _json_compact=lambda b: json.dumps(b), _overview_time_body=lambda category: {'time': 'today'},
            _session=SimpleNamespace(post=Mock(return_value=response)))
        fake._fetch_overview = lambda *a, **kw: ns['_fetch_overview'](fake, *a, **kw)
        with patch('zoneinfo.ZoneInfo', return_value=timezone.utc):
            rows = await ns['fetch_hourly_pv_history_day'](fake, self.day, 'UTC')
        self.assertEqual(len(rows), 24)
        self.assertEqual(json.loads(fake._session.post.call_args.kwargs['data']), {'time': '2026-10-02'})
        fake._account_device_count = 2
        fake._session.post.reset_mock()
        self.assertEqual(await ns['fetch_hourly_pv_history_day'](fake, self.day, 'UTC'), [])
        fake._session.post.assert_not_called()

    async def test_backfill_cache_executor_and_no_repeat_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            c = PvLearningCoordinatorMixin.__new__(PvLearningCoordinatorMixin)
            now = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
            c._pv_local_now = lambda: now
            c._site_timezone = timezone.utc
            c._entry = SimpleNamespace(entry_id='entry')
            c._pv_state_path = Path(directory)/'pv.json'
            c._pv_learning = SimpleNamespace(identity={'timezone': 'UTC'})
            async def executor(fn, *args): return fn(*args)
            c.hass = SimpleNamespace(config=SimpleNamespace(time_zone='UTC'), async_add_executor_job=AsyncMock(side_effect=executor))
            async def fetch(day, tz): return measured_pv_hours(props(points(day)), day, timezone.utc)
            c.api = SimpleNamespace(fetch_hourly_pv_history_day=AsyncMock(side_effect=fetch))
            c._maybe_train_hourly_pv = AsyncMock()
            await c._refresh_cloud_hourly_history(now)
            self.assertEqual(c.api.fetch_hourly_pv_history_day.await_count, 14)
            self.assertEqual(len(c._cloud_hourly_cache.days), 14)
            self.assertTrue((Path(directory)/'cloud_hourly_entry.json').exists())
            c._cloud_hourly_cache = None
            with patch('zoneinfo.ZoneInfo', return_value=timezone.utc):
                await c._refresh_cloud_hourly_history(now)
            self.assertEqual(c.api.fetch_hourly_pv_history_day.await_count, 14)
            self.assertEqual(c._maybe_train_hourly_pv.await_count, 2)
            self.assertFalse(hasattr(c, '_pv_calibrator'))


if __name__ == '__main__': unittest.main()
