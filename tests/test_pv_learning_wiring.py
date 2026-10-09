"""Real asynchronous learning methods with fake recorder/HTTP, no HA installed."""
import asyncio
import ast
import os
import sys
import tempfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch
sys.path.insert(0,os.path.join(os.path.dirname(__file__),'..'))
from hems.pv_coordinator import PvLearningCoordinatorMixin
from hems.pv_learning import PvLearningState,day_bounds
from pv_test_support import Checks,kyiv_2026

_check = Checks()
# All transport is mocked. aiohttp is an existing runtime dependency of HA,
# but these pure standalone tests also run without it installed locally.
try:
    import aiohttp
except ImportError:
    sys.modules['aiohttp'] = ModuleType('aiohttp')
from hems.forecast import ForecastService

async def exercise():
    with tempfile.TemporaryDirectory() as directory:
        c = PvLearningCoordinatorMixin.__new__(PvLearningCoordinatorMixin)
        now = datetime(2026,10,3,12,tzinfo=timezone.utc)
        c._site_timezone = timezone.utc
        c._pv_local_now = lambda:now
        c._pv_learning = PvLearningState('UTC',50.45,30.52)
        c._pv_calibrator = c._pv_learning.calibrator
        c._pv_matrix_at = c._archive_attempt_at = c._forecast_last_fetch = None
        c._pv_state_loaded = c._pv_state_dirty = False
        c._pv_state_path = Path(directory)/'entry.json'
        c._pv_legacy_path = Path(directory)/'legacy.json'
        c._pv_matrix,c._pv_actual = [],{}
        c.forecast_learned_ratio = .12
        c._history_entity = lambda key,fallback:fallback
        start = datetime(2026,9,3,tzinfo=timezone.utc)
        rows = [{'start':(start+timedelta(hours=h)).timestamp(),
                 'mean':1000. if 8 <= h%24 < 16 else 0} for h in range(30*24)]
        rec = ModuleType('homeassistant.components.recorder')
        def statistics(hass,start,end,ids,period,units,types):
            _check(period == 'hour','recorder queries hourly statistics')
            ent = next(iter(ids))
            return {ent:rows if types == {'mean'} else []}
        rec.statistics = SimpleNamespace(statistics_during_period=Mock(side_effect=statistics),
                                        get_metadata=Mock(return_value={}))
        c.hass = SimpleNamespace(async_add_executor_job=AsyncMock(side_effect=lambda f,*a:f(*a)))
        weather = [{'time':(now.replace(hour=0)+timedelta(hours=h)).strftime('%Y-%m-%dT%H:00'),
                    'timestamp':int((now.replace(hour=0)+timedelta(hours=h)).timestamp()),
                    'power_w':100.,'radiation_wm2':200.,'weather_code':0} for h in range(72)]
        daily = {d:SimpleNamespace(energy_kwh=v,dominant_weather_code=0) for d,v in
                 [('2026-10-03',1.),('2026-10-04',2.),('2026-10-05',3.)]}
        c._forecast = SimpleNamespace(
            get_archive_radiation=AsyncMock(return_value={(start+timedelta(days=d)).date().isoformat():4. for d in range(30)}),
            get_daily_forecasts=AsyncMock(return_value=daily),
            get_hourly_forecast=AsyncMock(return_value=weather),
            set_station_gain=Mock(return_value=True))
        with patch.dict(sys.modules,{'homeassistant.components.recorder':rec}):
            await c._maybe_refresh_pv_history(now)
            await c._maybe_refresh_pv_history(now+timedelta(minutes=59))
        _check(len(c._pv_matrix) == 30 and len(c._pv_actual) == 30,'real recorder wiring retains thirty dates')
        _check(rec.statistics.statistics_during_period.call_count == 2,'power/energy queries at most hourly')
        _check(c._pv_learning.model['gain'] == 2,'first refresh trains from existing facts and independent archive')
        _check(len(c._pv_calibrator) == 0,'archive backfill never invents forecast pairs')
        _check(c.forecast_tomorrow_kwh == 2 and c.forecast_day_after_kwh == 3,'forecast days selected by date')
        _check('2026-10-04' in c._pv_learning.snapshots and '2026-10-03' not in c._pv_learning.snapshots,'only future forecasts saved')
        _check(c._pv_state_path.exists(),'model and forecasts saved through executor')
        c._forecast.set_station_gain.assert_called_with(2.)
        # Instantiate the actual coordinator class with a minimal HA base.
        # Import its real HEMS helpers; only HA and cloud APIs are replaced.
        source = Path(__file__).resolve().parents[1]/'coordinator.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        nodes = []
        for node in tree.body:
            if isinstance(node,ast.Import):
                nodes.append(node)
            elif isinstance(node,ast.ImportFrom):
                if node.module.startswith('homeassistant') or node.module in ('api','const'):
                    continue
                node.level = 0
                nodes.append(node)
            elif isinstance(node,ast.ClassDef) and node.name == 'InverterCoordinator':
                nodes.append(node)
        class FakeBase:
            def __init__(self,hass,*args,**kwargs):
                self.hass,self.data = hass,{}
        ns = {'__name__':'coordinator_test','__package__':'coordinator_fixture',
              '_LOGGER':Mock(),'HomeAssistant':object,'ConfigEntry':object,
              'DataUpdateCoordinator':FakeBase,'InverterApiClient':object,
              'InverterOfflineError':RuntimeError,'TokenExpiredError':RuntimeError,
              'DOMAIN':'powmr_inverter','HISTORY_POLL_INTERVAL_SEC':900}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),ns)
        entry = SimpleNamespace(entry_id='actual-test',options={'predictive_mode':'shadow'},data={})
        c.hass.config = SimpleNamespace(time_zone='UTC')
        def update(entry,options):entry.options = options
        c.hass.config_entries = SimpleNamespace(async_update_entry=Mock(side_effect=update))
        with patch('hems.pv_coordinator.ZoneInfo',return_value=timezone.utc):
            real = ns['InverterCoordinator'](c.hass,SimpleNamespace(device_sn='actual'),entry)
        _check(ns['InverterCoordinator'].__new__(ns['InverterCoordinator'])._load_matrix_days == 30,
               'actual coordinator __new__ exposes default depth')
        real._pv_state_path = Path(directory)/'actual.json'
        real._pv_legacy_path = Path(directory)/'absent.json'
        real._pv_local_now = lambda:now
        real._history_entity = c._history_entity
        real._forecast = c._forecast
        real._execute_hems_command = AsyncMock()
        util = ModuleType('homeassistant.util')
        util.dt = SimpleNamespace(utcnow=lambda:now,as_local=lambda ts:ts,
                                  utc_from_timestamp=lambda ts:datetime.fromtimestamp(ts,timezone.utc))
        package = ModuleType('coordinator_fixture')
        package.__path__ = [str(source.parent)]
        with patch.dict(sys.modules,{'homeassistant.components.recorder':rec,'homeassistant.util':util,
                'coordinator_fixture':package,'coordinator_fixture.hems':sys.modules['hems'],
                'coordinator_fixture.hems.history_builder':sys.modules['hems.history_builder']}):
            await real._run_hems_engine({'outputSourcePriority':'0','chargerSourcePriority':'2',
                'batteryPower':0,'pvPower':0,'gridPower':500,'loadPower':500,'gridVoltage':230},60,now.replace(tzinfo=None))
        _check(len(real._hems._consumption_history) == 30,'actual engine receives thirty-day load history')
        _check(real._hems._last_predictive_hint is not None and real._hems._last_predictive_plan is not None,
               'actual Shadow evaluation keeps hint and plan')
        _check(real._hems._predictive_controller.calibrator is real._pv_calibrator,
               'calibrator wired before first actual engine evaluation')
        _check(entry.options['predictive_mode'] == 'shadow','actual flow never enables Assist')
        before = dict(c._pv_learning.model)
        c._pv_learning.archive_checked_day = None
        c._archive_attempt_at = None
        c._forecast.get_archive_radiation = AsyncMock(side_effect=RuntimeError('offline'))
        await c._maybe_train_pv_station(now)
        await c._maybe_train_pv_station(now+timedelta(minutes=1))
        _check(c._pv_learning.model == before and c._forecast.get_archive_radiation.call_count == 1,'archive error retains model and throttles retry')
        c._forecast_last_fetch = None
        c._forecast.get_daily_forecasts = AsyncMock(side_effect=RuntimeError('offline'))
        await c._maybe_refresh_forecast(now)
        _check(c.forecast_tomorrow_kwh is None and not c.hourly_forecast_today,'failed forecast cannot reuse stale day')
        c._pv_matrix_at = None
        rec.statistics.statistics_during_period = Mock(side_effect=RuntimeError('recorder offline'))
        with patch.dict(sys.modules,{'homeassistant.components.recorder':rec}):
            await c._maybe_refresh_pv_history(now)
            await c._maybe_refresh_pv_history(now+timedelta(minutes=5))
        _check(rec.statistics.statistics_during_period.call_count == 1,'recorder failures also respect hour limit')
    # Test actual transport conversion and station gain cache invalidation.
    tz = kyiv_2026()
    # Patch ZoneInfo to the test zone for the whole block.
    # Freeze datetime.now() to 2026-10-02 so the trim keeps
    # the rows whose local date is 2026-10-02.
    import hems.forecast as forecast_mod
    real_datetime = forecast_mod.datetime
    real_zoneinfo = forecast_mod.ZoneInfo

    class _FrozenDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            base = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
            return base if tz is None else base.astimezone(tz)

    forecast_mod.datetime = _FrozenDateTime
    forecast_mod.ZoneInfo = lambda _name: tz
    try:
        f = ForecastService(timezone_name='Test/Kyiv2026')
        f._hourly_cache,f._daily_cache = (1,[]),(1,{})
        _check(f.set_station_gain(2) and f._hourly_cache is None and f._daily_cache is None,'gain invalidates both forecast caches')
        data = {'hourly':{'time':[int(datetime(2026,10,2,10,tzinfo=timezone.utc).timestamp())],
                          'shortwave_radiation':[1000],'weather_code':[0]}}
        class Response:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            def raise_for_status(self):pass
            async def json(self):return data
        session = SimpleNamespace(get=Mock(return_value=Response()))
        f._ensure_session = AsyncMock(return_value=session)
        f._rate_limit = AsyncMock()
        result = await f._fetch_hourly()
        _check(result[0]['power_w'] == 2000,'trained gain applied at common forecast source')
        # Contract v2: ``time`` is the START of the radiation interval
        # (api_t - 1h = 09:00 UTC = 12:00 Kyiv in EEST). Previously
        # this was 10:00 UTC = 13:00 Kyiv.
        _check(result[0]['time'] == '2026-10-02T12:00','forecast radiation interval start in local timezone')
        # Contract v2: the production code asks for 4 days from
        # the API so the interval-shift does not eat the last
        # local hour of the third day, then trims to 3 local
        # calendar days.
        _check(session.get.call_args.kwargs['params']['forecast_days'] == 4,
           'four forecast days requested (one extra so the last '
           'local hour of day 3 survives the interval-shift)')
        f.set_station_gain(100)
        result = await f._fetch_hourly()
        _check(result[0]['power_w'] == 20000,'PV power capped at twenty kW')
        data['hourly']['shortwave_radiation'] = [None]
        _check(not await f._fetch_hourly(),'unknown radiation never becomes measured zero')
        # Test the actual archive transport on a 25-hour local day.
        start = datetime(2026,10,24,tzinfo=timezone.utc)
        data['hourly'] = {'time':[int((start+timedelta(hours=h)).timestamp()) for h in range(48)],
                      'shortwave_radiation':[500.]*48}
        session.get.reset_mock()
        day = datetime(2026,10,25).date()
        archive = await f.get_archive_radiation(day,day)
        _check(archive == {'2026-10-25':12.5},'archive UTC hours integrate twenty-five-hour local day')
        _check(session.get.call_count == 1 and session.get.call_args.kwargs['params']['timezone'] == 'UTC',
           'archive fetched in one bounded UTC request')
        data['hourly']['shortwave_radiation'][24] = None
        _check(not await f.get_archive_radiation(day,day),'incomplete archive day excluded from station training')
    finally:
        forecast_mod.datetime = real_datetime

asyncio.run(exercise())
# Verify integration ordering on the real coordinator, not a duplicate stub.
tree = ast.parse((Path(__file__).resolve().parents[1]/'coordinator.py').read_text(encoding='utf-8'))
cls = next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name == 'InverterCoordinator')
method = next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name == '_run_hems_engine')
calls = [(n.lineno,n.func.attr) for n in ast.walk(method) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)]
_check(next(line for line,name in calls if name == '_maybe_refresh_pv_history') < next(line for line,name in calls if name == 'evaluate'),'learning runs before existing engine evaluation')
_check.finish()
