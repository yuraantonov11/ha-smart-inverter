import sys
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hems.pv_chart import chart_points, previous_curve
from types import SimpleNamespace


class PvChartTests(unittest.TestCase):
    now = datetime(2026, 10, 3, 12, tzinfo=timezone(timedelta(hours=3)))

    def row(self, time='11:30:00', value=.035, real=True):
        return {'time': f'2026-10-03 {time}', 'value': value, 'isRealValue': real}

    def test_kw_to_w_once_preserves_half_hours_and_zero(self):
        points=chart_points([self.row(), self.row('00:00:00', 0)], self.now)
        self.assertEqual([p['power_w'] for p in points], [0,35])
        self.assertEqual(points[-1]['time'], '2026-10-03T11:30:00+03:00')

    def test_future_placeholder_and_other_date_are_absent(self):
        rows=[self.row('13:00:00'), self.row(real=False), {**self.row(),'time':'2026-10-02 11:30:00'}]
        self.assertEqual(chart_points(rows,self.now), [])

    def test_missing_invalid_and_conflict_not_zero(self):
        for value in (None, True, -1, float('nan'), float('inf')):
            self.assertEqual(chart_points([self.row(value=value)],self.now), [])
        self.assertEqual(chart_points([self.row(),self.row(value=.2)],self.now), [])
        self.assertEqual(len(chart_points([self.row(),self.row()],self.now)),1)

    def test_yesterday_reference_has_its_own_date(self):
        ts=datetime(2026,10,2,16,tzinfo=self.now.tzinfo).timestamp()
        cache=SimpleNamespace(days={'2026-10-02':[{'start':ts,'mean':485}]})
        curve=previous_curve(cache,self.now)
        self.assertEqual(curve['date'],'2026-10-02')
        self.assertEqual(curve['points'][0]['power_w'],485)
        self.assertEqual(previous_curve(None,self.now)['points'],[])

    def test_raw_previous_peak_is_not_averaged_or_shifted(self):
        ts=datetime(2026,10,2,16,tzinfo=self.now.tzinfo).timestamp()
        samples=[{'time':'2026-10-02T16:00:00+03:00','power_w':478},
                 {'time':'2026-10-02T16:30:00+03:00','power_w':492}]
        cache=SimpleNamespace(days={'2026-10-02':[{'start':ts,'mean':485,'samples':samples}]})
        curve=previous_curve(cache,self.now)
        self.assertEqual(curve['basis'],'cloud_half_hour_samples')
        self.assertEqual(curve['points'],samples)


if __name__=='__main__': unittest.main()
