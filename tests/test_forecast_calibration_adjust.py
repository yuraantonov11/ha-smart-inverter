"""Daily bias correction uses real kWh samples and never compounds."""
import os
import sys
from datetime import datetime, timezone
from unittest.mock import Mock, patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from hems.forecast_calibration import ForecastCalibrator
from hems.pv_coordinator import PvLearningCoordinatorMixin
from pv_test_support import Checks

check = Checks()
c = PvLearningCoordinatorMixin.__new__(PvLearningCoordinatorMixin)
c._pv_local_now = lambda: datetime(2026, 10, 1, tzinfo=timezone.utc)
c._raw_forecast_kwh = {'2026-10-02': 5., '2026-10-03': 8.}
c._pv_calibrator = ForecastCalibrator(unit='kWh')
c.forecast_tomorrow_kwh, c.forecast_day_after_kwh = 5., 8.
for _ in range(4):
    c._pv_calibrator.record(5., 4.)
with patch('hems.pv_coordinator._LOGGER') as log:
    c._adjust_daily_forecasts()
    check(c.forecast_tomorrow_kwh == 4. and c.forecast_day_after_kwh == 7., 'negative 1 kWh bias corrects both horizons')
    check(log.info.call_count == 2, 'both changed horizons logged')
    c._adjust_daily_forecasts()
    check(c.forecast_tomorrow_kwh == 4. and log.info.call_count == 2, 'repeated adjustment neither compounds nor relogs')
check(c._raw_forecast_kwh['2026-10-02'] == 5., 'issued forecast remains raw')
for actual, expected, label in ((5., 5., 'zero bias'), (4.6, 5., 'below ten percent'), (4.5, 5., 'exactly ten percent')):
    c._pv_calibrator.reset()
    for _ in range(4):
        c._pv_calibrator.record(5., actual)
    c._adjust_daily_forecasts()
    check(c.forecast_tomorrow_kwh == expected, label+' does not adjust')
c._pv_calibrator.reset()
c._pv_calibrator.record(5., 4.)
c._adjust_daily_forecasts()
check(c.forecast_tomorrow_kwh == 5., 'one sample retains minimum-four guard')
c._raw_forecast_kwh['2026-10-02'] = None
c._adjust_daily_forecasts()
check(c.forecast_tomorrow_kwh is None, 'missing forecast remains unknown')
c._raw_forecast_kwh['2026-10-02'] = 5.
c._pv_calibrator.reset()
for _ in range(4):
    c._pv_calibrator.record(5., 0.)
c._adjust_daily_forecasts()
check(c.forecast_tomorrow_kwh == 2.5, 'existing fifty-percent correction limit retained')
check(c._pv_calibrator.unit == 'kWh', 'daily correction never mixes Watts with kWh')
check.finish()
