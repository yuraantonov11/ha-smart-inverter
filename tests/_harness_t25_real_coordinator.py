"""T25 real-coordinator harness — exercises
the *production*
``_persist_*``, ``_maybe_persist_*`` and
``shutdown`` implementations from
``coordinator.py`` against a stub that
owns the real
``hass.config_entries.async_update_entry``
side-effect.

The audit demanded that the T25 flush
test call the production
``coordinator.shutdown()`` end-to-end —
a stub that re-implements the anchor
clearing or the throttle bypass would
not pin the contract.

Strategy:

1. Load the production
   ``powmr_inverter.coordinator``
   module via ``importlib`` so the
   relative imports
   (``from .api import ...``) resolve
   to the real package.
2. Take ``InverterCoordinator``
   *attribute* ``_persist_*`` /
   ``_maybe_persist_*`` /
   ``shutdown`` and bind them onto a
   plain object that owns the
   dependencies the methods read
   (``self.hass``, ``self._entry``,
   ``self._schedule_rules``,
   ``self._battery_soh``,
   ``self._demand_forecast``).
3. The stub's side-effects (file I/O,
   ``async_update_entry``) go to a
   fake ``hass``; everything else is
   production.

The harness has no shims for unknown
globals: every name the production
code needs is imported from the
production module and the harness
deliberately raises
``AttributeError`` if a name is
missing — the audit forbade masking
``NameError``.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The integration lives at the repo
# root — import it as ``powmr_inverter``
# via a synthetic package so the
# relative imports inside the
# production modules resolve.
# R10.6 (round 5): the
# ``os.chdir("/tmp")``
# shadow-hack was
# removed. The
# integration's
# ``select.py`` is
# only an issue on
# Windows where the
# CWD is the tests
# directory; the
# importlib loader
# below uses absolute
# paths so no global
# chdir is needed.
sys.path.insert(0, str(REPO_ROOT))


def _load_integration_submodule(name: str) -> types.ModuleType:
    """Load
    ``powmr_inverter.<name>`` from disk
    via ``importlib``. The first call
    also bootstraps the synthetic
    package + ``const``.

    The name can be a dotted path
    (``hems.schedule_rules``); the
    matching file is looked up
    relative to ``REPO_ROOT``."""

    if "powmr_inverter" not in sys.modules:
        pkg = types.ModuleType("powmr_inverter")
        pkg.__path__ = [str(REPO_ROOT)]
        sys.modules["powmr_inverter"] = pkg

        spec = importlib.util.spec_from_file_location(
            "powmr_inverter.const",
            REPO_ROOT / "const.py",
        )
        const_mod = importlib.util.module_from_spec(spec)
        sys.modules["powmr_inverter.const"] = const_mod
        spec.loader.exec_module(const_mod)
        pkg.DOMAIN = const_mod.DOMAIN

    if "/" in name or "." in name:
        sep = "/" if "/" in name else "."
        # Subpackage like
        # ``services`` or
        # ``hems``. The file
        # is at
        # ``<repo>/<a>/<b>.py``
        # for dotted.
        parts = name.split(".")
        if "/" in name:
            sub, _, _rest = name.partition("/")
            file_path = (
                REPO_ROOT / sub / "__init__.py"
            )
        else:
            file_path = REPO_ROOT / "/".join(parts)
            file_path = file_path.with_suffix(".py")
        # Ensure intermediate
        # subpackage modules
        # exist so ``from
        # ..const import
        # DOMAIN`` resolves.
        cumulative = "powmr_inverter"
        for p in parts[:-1]:
            cumulative = f"{cumulative}.{p}"
            if cumulative not in sys.modules:
                m = types.ModuleType(cumulative)
                m.__path__ = [
                    str(REPO_ROOT / "/".join(parts[: parts.index(p) + 1]))
                ]
                sys.modules[cumulative] = m
    else:
        # Could be either a
        # top-level ``.py``
        # file (``coordinator``)
        # OR a subpackage
        # (``services``). Pick
        # the one that exists
        # on disk.
        top = REPO_ROOT / f"{name}.py"
        sub_init = (
            REPO_ROOT / name / "__init__.py"
        )
        if top.exists():
            file_path = top
        elif sub_init.exists():
            file_path = sub_init
            # Register the
            # subpackage so
            # ``from ..const``
            # resolves.
            full = f"powmr_inverter.{name}"
            if full not in sys.modules:
                m = types.ModuleType(full)
                m.__path__ = [
                    str(REPO_ROOT / name)
                ]
                sys.modules[full] = m
        else:
            file_path = top
    spec = importlib.util.spec_from_file_location(
        f"powmr_inverter.{name}",
        file_path,
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"powmr_inverter.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


# Force the integration to register
# itself as ``powmr_inverter.*`` so
# submodules can import each other.
_coordinator = _load_integration_submodule("coordinator")
_InverterCoordinator = _coordinator.InverterCoordinator

# Pre-import hems so the coordinator
# module's ``from .hems.X`` resolves.
_load_integration_submodule("hems.schedule_rules")
_load_integration_submodule("hems.battery_soh")
_load_integration_submodule("hems.demand_forecast")

from hems.battery_soh import BatterySoH
from hems.demand_forecast import DemandForecastService
from hems.schedule_rules import ScheduleRule, ScheduleRulesService

_LOGGER = logging.getLogger("powmr_inverter.tests.harness")


# ---------------------------------------------------------------------------
# Stub Home Assistant surface
# ---------------------------------------------------------------------------


class _StubConfigEntries:
    """Replacement for
    ``hass.config_entries.async_update_entry``.

    Records every write. The
    ``fail_once`` flag makes the next
    call raise ``RuntimeError`` (the
    production ``_persist_*``
    methods catch any exception and
    return ``False``)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fail_next: bool = False

    def async_update_entry(self, entry, *, options) -> None:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError(
                "stub: persist disabled for this call"
            )
        # Mirror the production
        # side-effect exactly: the new
        # ``options`` replace the old
        # ones on the entry object.
        entry.options = dict(options)
        self.calls.append(
            {
                "entry_id": entry.entry_id,
                "options_keys": sorted(options.keys()),
            }
        )


class _StubHass:
    """Minimal HA surface — only the
    methods the production
    ``_persist_*`` / ``shutdown``
    call. Anything else raises
    ``AttributeError`` (we *don't*
    mask unknown globals)."""

    def __init__(self) -> None:
        self.config_entries = _StubConfigEntries()


class _StubEntry:
    def __init__(
        self,
        *,
        entry_id: str = "01M3XWJ8DRYDQC8A0NCPRVB53N",
        options: dict | None = None,
    ) -> None:
        self.entry_id = entry_id
        self.options = dict(options or {})
        self.data: dict = {}


# ---------------------------------------------------------------------------
# Real-coordinator stub: production methods, stub side-effects
# ---------------------------------------------------------------------------


_PRODUCTION_METHOD_NAMES = (
    "_persist_schedule_rules",
    "_persist_battery_soh",
    "_maybe_persist_battery_soh",
    "_persist_demand_forecast",
    "_maybe_persist_demand_forecast",
    "shutdown",
)


class _RealCoordinatorStub:
    """Coordinator stub that owns the
    *real* production methods
    (``_persist_*``, ``_maybe_persist_*``,
    ``shutdown``) and a real
    ``ScheduleRulesService`` /
    ``BatterySoH`` /
    ``DemandForecastService``.

    The stub's side-effects
    (``async_update_entry``) go to the
    fake ``hass``; everything else is
    production."""

    # Match the production throttle
    # constants — the production
    # methods read them off ``self``
    # via ``self._SOH_PERSIST_MIN_INTERVAL_S``.
    _SOH_PERSIST_MIN_INTERVAL_S = 30.0
    _DEMAND_PERSIST_MIN_INTERVAL_S = 60.0

    def __init__(
        self,
        *,
        persist_ok: bool = True,
        options: dict | None = None,
        entry_id: str = "01M3XWJ8DRYDQC8A0NCPRVB53N",
    ) -> None:
        # Production code reads
        # ``self._entry``; mirror the
        # exact name.
        self._entry = _StubEntry(
            entry_id=entry_id,
            options=options,
        )
        self.entry = self._entry
        self.hass = _StubHass()
        self.hass.config_entries.fail_next = not persist_ok
        self._schedule_rules = ScheduleRulesService()
        self._battery_soh = BatterySoH(cycle_count=0)
        self._demand_forecast = DemandForecastService()
        self._last_soh_persist_at: datetime | None = None
        self._last_demand_persist_at: datetime | None = None
        # Attributes the production
        # ``shutdown`` references
        # (``_forecast``,
        # ``_demand_forecast``) — set
        # to ``None`` so the
        # ``getattr(... None)`` guard
        # skips them.
        self._forecast = None
        self._demand_forecast_close = None
        # Bind the *real* production
        # methods onto this instance.
        for name in _PRODUCTION_METHOD_NAMES:
            fn = getattr(_InverterCoordinator, name)
            setattr(self, name, types.MethodType(fn, self))

    # Convenience for tests: simulate
    # ``track_soc`` raising cycle_count
    # without dragging in the rest of
    # the coordinator init.
    def track_soc(self, soc: float) -> None:
        self._battery_soh.track_soc(soc)


def make_coordinator(
    *,
    persist_ok: bool = True,
    options: dict | None = None,
    entry_id: str = "01M3XWJ8DRYDQC8A0NCPRVB53N",
) -> _RealCoordinatorStub:
    """Build a fresh coordinator stub
    wired to the real production
    methods. ``persist_ok=False``
    flips the next
    ``async_update_entry`` to raise."""
    return _RealCoordinatorStub(
        persist_ok=persist_ok,
        options=options,
        entry_id=entry_id,
    )


# Re-export for tests
__all__ = [
    "BatterySoH",
    "DemandForecastService",
    "ScheduleRule",
    "ScheduleRulesService",
    "_InverterCoordinator",
    "_PRODUCTION_METHOD_NAMES",
    "make_coordinator",
]