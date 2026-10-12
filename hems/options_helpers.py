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
     via ``entry.options`` — OR, when neither
     the user nor the options flow have set
     them, the Home Assistant location
     (``hass.config.latitude`` /
     ``hass.config.longitude``) is the next
     source of truth. The canonical
     integration defaults are only the
     absolute last-resort fallback.
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
    """Resolve the active site identity.

    Returns a plain dict with the keys
    ``latitude``, ``longitude``, and
    ``source``. ``source`` is one of:

      * ``"entry"`` — the user explicitly
        set ``site_latitude`` and
        ``site_longitude`` in the options
        flow (or via ``async_update_entry``).
        This is the highest-priority source
        because the user can intentionally
        decouple the forecast station from
        the HA location (a coastal home with
        an inland mountain PV site, for
        example).
      * ``"ha"`` — the user did NOT set the
        station coordinates; the next valid
        source is the Home Assistant
        configuration. This is the production
        default: HA's own location IS the site
        unless the user has overridden it.
      * ``"default"`` — neither the entry
        options nor HA had a usable value
        (both ``None`` or both invalid); the
        canonical integration defaults are
        the last-resort fallback so the
        forecast service can still answer
        requests.

    The helper accepts ``None`` for both
    ``ha_latitude`` and ``ha_longitude``
    and treats them as "no value", NOT as
    the coordinate 0.0. Out-of-range
    numbers are also treated as "no value"
    so a misconfigured HA does not pin the
    forecast to a nonsense coordinate.
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
        "latitude": DEFAULT_SITE_LATITUDE,
        "longitude": DEFAULT_SITE_LONGITUDE,
        "source": "default",
    }


def compute_site_coordinates(
    options: dict | None,
    *,
    ha_latitude: float | None = None,
    ha_longitude: float | None = None,
) -> tuple[float, float]:
    """Read site coordinates for the forecast
    and PV-learning stack.

    Resolution order — the audit forbids the
    legacy silent Kyiv fallback as the
    first choice. The first source that has
    both a valid latitude and a valid
    longitude wins:

      1. ``entry.options["site_latitude"]``
         and
         ``entry.options["site_longitude"]``
         when both are valid numbers. This
         is the user-overridden station
         location (the config flow exposes
         this as the "Степніва координати"
         field).
      2. ``ha_latitude`` / ``ha_longitude``
         from ``hass.config`` when the user
         did NOT override the station
         coordinates. HA's own location IS
         the site by production default —
         the previous behaviour of always
         using the Kyiv constant when the
         user did not override was a defect
         for any HA instance running outside
         the Kyiv region.
      3. ``DEFAULT_SITE_LATITUDE`` /
         ``DEFAULT_SITE_LONGITUDE`` as the
         absolute last-resort fallback so
         the forecast service can still
         answer.

    ``None`` is never a valid coordinate.
    The function rejects ``None``,
    booleans, non-numeric strings, ``NaN``,
    and out-of-range numbers at every source
    before falling through to the next one.

    Callers that need to know WHICH source
    won (so they can expose it to UI,
    audit, and the on-disk journal)
    should call :func:`resolve_site_identity`
    directly.
    """
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