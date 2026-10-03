"""Timestamped cloud PV points for display; never fills missing observations."""
from datetime import datetime, timedelta, timezone
from .pv_learning import finite, timestamp


def chart_points(records, now):
    points, conflicts = {}, set()
    for row in records:
        if not isinstance(row, dict) or row.get('isRealValue') is not True:
            continue
        try:
            ts = datetime.fromisoformat(row['time'])
            if ts.tzinfo is None:
                a, b = ts.replace(tzinfo=now.tzinfo, fold=0), ts.replace(tzinfo=now.tzinfo, fold=1)
                if a.utcoffset() != b.utcoffset():
                    continue
                ts = a
            ts = ts.astimezone(now.tzinfo)
            value = None if isinstance(row.get('value'), bool) else finite(row.get('value'), high=20)
            if value is None or ts.date() != now.date() or ts > now:
                continue
            key = ts.timestamp()
            point = {'time': ts.isoformat(), 'power_w': round(value * 1000, 3)}
            if key in points and points[key] != point:
                conflicts.add(key)
            points[key] = point
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    return [points[k] for k in sorted(points) if k not in conflicts]


def previous_curve(cache, now):
    day = (now.date() - timedelta(days=1)).isoformat()
    rows = cache.days.get(day, []) if cache else []
    return {'date': day, 'points': [
        {'time': timestamp(r['start']).astimezone(now.tzinfo).isoformat(), 'power_w': r['mean']}
        for r in rows]}
