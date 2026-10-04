"""Pure option helpers — T16 audit.

These helpers are intentionally *pure* and
*HA-free*: they accept plain values (option
dicts, ``ConfigEntry``-like namespaces) and
return plain values. They live outside the
``__init__.py`` entry point so that the
T16 behavioural test suite can import them
without pulling in the homeassistant
module.

The audit requires:
  1. ``poll_interval`` is read from
     ``entry.options`` (not hard-coded) and
     clamped to ``MIN_POLL_INTERVAL_SEC``.
  2. ``site_latitude`` / ``site_longitude``
     come from the integration's defaults
     unless the user has overridden them
     via ``entry.options``.
  3. The reload-required classifier is
     applied here, so the options flow and
     the runtime can share the *same*
     decision function.
"""
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
DEFAULT_SITE_LATITUDE: float = 50.45
DEFAULT_SITE_LONGITUDE: float = 30.52


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


def compute_site_coordinates(
    options: dict | None,
) -> tuple[float, float]:
    """Read ``site_latitude`` /
    ``site_longitude`` from ``entry.options``
    with a fallback to the integration's
    canonical defaults.

    Returns ``(latitude, longitude)``. A
    legacy entry without either key gets
    the defaults — same value, single
    source of truth.
    """
    if options is None:
        return (DEFAULT_SITE_LATITUDE, DEFAULT_SITE_LONGITUDE)
    lat = float(options.get("site_latitude", DEFAULT_SITE_LATITUDE))
    lon = float(options.get("site_longitude", DEFAULT_SITE_LONGITUDE))
    return (lat, lon)


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
