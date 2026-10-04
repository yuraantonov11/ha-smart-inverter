"""Package-neutral defaults for the HEMS subpackage.

This module exists so the HEMS subpackage
(``hems/*.py``) and the root integration
(``*.py`` at the integration root) can share
the same canonical constants without one side
breaking the other.

Why not import from the root ``const.py``?
The root ``const.py`` is the integration-level
constants module. ``hems/`` is a subpackage.
A relative import ``from ..const import ...``
works when Home Assistant loads the integration
as ``custom_components.powmr_inverter`` — but
it *fails* for the standalone test runner,
which imports ``hems`` as a top-level package
(``tests/test_*.py`` doing
``from hems.pv_coordinator import ...``). The
failure mode is
``ImportError: attempted relative import
beyond top-level package``.

Why not put the values in ``hems/__init__.py``?
That works but it leaks HEMS internals into
the package import surface. A dedicated
``defaults.py`` is explicit about intent: the
constants defined here are the canonical
defaults for HEMS-owned state, and any other
module (root or subpackage) is free to import
them.

This module must not import from the root
``const.py`` (that would re-create the same
circular-import problem we are solving). It
must be a leaf module in the dependency graph.
"""
from __future__ import annotations

#: Default site latitude (Kyiv). T16 audit: this
#: value is the single source of truth for the
#: integration's site-coordinate default. The
#: config flow (UI) and the
#: ``PvLearningCoordinatorMixin`` (runtime) both
#: read this constant. The audit requires the
#: two to agree exactly so a fresh install does
#: not silently switch coordinates between the
#: form preview and the running plugin.
DEFAULT_SITE_LATITUDE: float = 50.45

#: Default site longitude (Kyiv). See
#: ``DEFAULT_SITE_LATITUDE`` for the audit
#: rationale.
DEFAULT_SITE_LONGITUDE: float = 30.52
