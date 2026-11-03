"""HA-free option helpers. Location is entry override, then HA, else unset."""
from __future__ import annotations

# T16 audit: these constants are the
# canonical runtime defaults. The root
# ``const.py`` and ``hems/defaults.py``
# keep their own copies; the test suite
# asserts the three values are in
# lock-step. Duplication is intentional —
# the alternative is a ``..const`` import
# that breaks the standalone test runner.
DEFAULT_POLL_INTERVAL_SEC: int = 5
MIN_POLL_INTERVAL_SEC: int = 3
# Legacy exports retained for compatibility; never a runtime location fallback.
DEFAULT_SITE_LATITUDE: float = 50.45
DEFAULT_SITE_LONGITUDE: float = 30.52


def _is_valid_latitude(value) -> bool:
    """``True`` if ``value`` is a real number
    in the geographic latitude range
    ``[-90, +90]``. ``None``, booleans,
    strings, ``NaN``, and out-of-range
    numbers are all rejected — ``None`` is
    never a valid coordinate.
    """
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    if value != value:  # NaN
        return False
    return -90.0 <= float(value) <= 90.0


def _is_valid_longitude(value) -> bool:
    """``True`` if ``value`` is a real number
    in the geographic longitude range
    ``[-180, +180]``. Same reject rules as
    ``_is_valid_latitude``.
    """
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    if value != value:  # NaN
        return False
    return -180.0 <= float(value) <= 180.0


def compute_poll_interval(
    options: dict | None,
) -> int:
    """Read ``poll_interval`` from ``entry.options``
    and clamp to ``MIN_POLL_INTERVAL_SEC``.

    A legacy entry without the key falls
    back to ``DEFAULT_POLL_INTERVAL_SEC``.
    The clamp is the production safety net
    for hand-edited entries that bypass
    the config flow's ``vol.Range`` check.
    """
    if options is None:
        return DEFAULT_POLL_INTERVAL_SEC
    raw = int(options.get("poll_interval", DEFAULT_POLL_INTERVAL_SEC))
    if raw < MIN_POLL_INTERVAL_SEC:
        return MIN_POLL_INTERVAL_SEC
    return raw


def resolve_site_identity(
    options: dict | None,
    *,
    ha_latitude: float | None = None,
    ha_longitude: float | None = None,
) -> dict:
    """Resolve entry overrides, otherwise the valid Home Assistant location.

    Without either complete coordinate pair, return ``source=unconfigured``
    and no coordinates. Forecast HTTP requests must then abstain; choosing a
    convenient city would manufacture a forecast for the wrong installation.
    """
    if options is not None:
        lat_raw = options.get("site_latitude")
        lon_raw = options.get("site_longitude")
        if _is_valid_latitude(lat_raw) and _is_valid_longitude(lon_raw):
            return {
                "latitude": float(lat_raw),
                "longitude": float(lon_raw),
                "source": "entry",
            }
    if _is_valid_latitude(ha_latitude) and _is_valid_longitude(ha_longitude):
        return {
            "latitude": float(ha_latitude),
            "longitude": float(ha_longitude),
            "source": "ha",
        }
    return {
        "latitude": None,
        "longitude": None,
        "source": "unconfigured",
    }


def compute_site_coordinates(
    options: dict | None,
    *,
    ha_latitude: float | None = None,
    ha_longitude: float | None = None,
) -> tuple[float | None, float | None]:
    """Return the station or HA coordinates, or ``(None, None)`` if unset."""
    identity = resolve_site_identity(
        options,
        ha_latitude=ha_latitude,
        ha_longitude=ha_longitude,
    )
    return identity["latitude"], identity["longitude"]


def compute_reserve_soc(
    options: dict | None,
    default: float = 20.0,
) -> float:
    """Read ``reserve_soc`` from
    ``entry.options`` with a fallback.

    Exposed as a helper so the test suite
    can assert that the value the
    coordinator will see matches the
    value the user submitted in the
    options flow.
    """
    if options is None:
        return default
    return float(options.get("reserve_soc", default))


#: T16 audit: keys that require a full
#: setup reload. Other keys are applied
#: selectively (the coordinator reads them
#: on every cycle) and do not trigger a
#: reload. Kept in lock-step with
#: ``__init__._RELOAD_REQUIRED_OPTION_KEYS``
#: (test_16_06 asserts the equality).
RELOAD_REQUIRED_OPTION_KEYS: frozenset[str] = frozenset(
    {
        "poll_interval",
        "email",
        "password",
        # T16 audit follow-up: site
        # coordinates feed the PV-learning
        # state at construction time. A
        # coordinate change must be applied
        # via a full reload so the
        # ``_pv_learning`` state is rebuilt
        # from scratch.
        "site_latitude",
        "site_longitude",
    }
)


def requires_reload(
    new_options: dict,
    old_options: dict | None,
) -> bool:
    """Return ``True`` if any reload-required
    key changed between ``old_options`` and
    ``new_options``.

    ``old_options`` is the previously
    persisted option dict (or ``None`` for
    a fresh entry). A key is "new" if it
    was absent from ``old_options`` and is
    now present, or if its value differs.
    """
    if old_options is None:
        old_options = {}
    for key in RELOAD_REQUIRED_OPTION_KEYS:
        if new_options.get(key) != old_options.get(key):
            return True
    return False
