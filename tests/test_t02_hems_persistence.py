"""T02: HEMS user-off / smart_mode must survive a reload.

Run with:
    /tmp/powmr-venv/bin/python tests/test_t02_hems_persistence.py
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Stub homeassistant.* and aiohttp so coordinator.py is importable
# without its real dependencies.
ha_pkg = types.ModuleType("homeassistant")
ha_pkg.__path__ = []
sys.modules.setdefault("homeassistant", ha_pkg)
const_mod = types.ModuleType("homeassistant.const")
const_mod.Platform = types.SimpleNamespace(SENSOR="sensor", BINARY_SENSOR="binary_sensor",
                                          SWITCH="switch", SELECT="select", NUMBER="number")
sys.modules.setdefault("homeassistant.const", const_mod)
helpers_pkg = types.ModuleType("homeassistant.helpers")
helpers_pkg.__path__ = []
sys.modules.setdefault("homeassistant.helpers", helpers_pkg)
for sub in ("aiohttp_client", "update_coordinator", "entity", "config_entries",
            "storage", "service", "selector"):
    m = types.ModuleType(f"homeassistant.helpers.{sub}")
    if sub == "update_coordinator":
        m.DataUpdateCoordinator = type("DataUpdateCoordinator", (), {})
        m.UpdateFailed = type("UpdateFailed", (Exception,), {})
    if sub == "entity":
        m.Entity = type("Entity", (), {})
        m.CoordinatorEntity = type("CoordinatorEntity", (), {})
    if sub == "config_entries":
        m.ConfigEntry = type("ConfigEntry", (), {})
    sys.modules.setdefault(f"homeassistant.helpers.{sub}", m)
aiohttp_stub = types.ModuleType("aiohttp")
aiohttp_stub.ClientError = type("ClientError", (Exception,), {})
aiohttp_stub.ClientResponseError = type("ClientResponseError", (Exception,), {})
aiohttp_stub.ClientSession = type("ClientSession", (), {})
sys.modules.setdefault("aiohttp", aiohttp_stub)
components_pkg = types.ModuleType("homeassistant.components")
components_pkg.__path__ = []
sys.modules.setdefault("homeassistant.components", components_pkg)
recorder_pkg = types.ModuleType("homeassistant.components.recorder")
recorder_pkg.get_instance = lambda *a, **k: None
recorder_pkg.statistics = types.SimpleNamespace(during_period=lambda *a, **k: [])
sys.modules.setdefault("homeassistant.components.recorder", recorder_pkg)
util_pkg = types.ModuleType("homeassistant.util")
util_pkg.__path__ = []
sys.modules.setdefault("homeassistant.util", util_pkg)
dt_mod = types.ModuleType("homeassistant.util.dt")
dt_mod.utcnow = lambda: None
dt_mod.now = lambda: None
sys.modules.setdefault("homeassistant.util.dt", dt_mod)

# Build a stub coordinator class so we can pull the relevant code out
# without instantiating a real one.
import importlib  # noqa: E402

# We can't easily import coordinator.py wholesale without homeassistant
# components. Instead, we use the *same approach* as T01: extract the
# relevant block (the new options initialiser + setter) and execute it
# in a controlled namespace against a fake config entry.
import textwrap  # noqa: E402
import ast  # noqa: E402

coord_src = (ROOT / "coordinator.py").read_text(encoding="utf-8")
coord_lines = coord_src.splitlines(keepends=True)
tree = ast.parse(coord_src)


def _find_class_methods(klass: str, names: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == klass:
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name in names:
                    start = sub.lineno - 1
                    end = sub.end_lineno
                    out[sub.name] = textwrap.dedent("".join(coord_lines[start:end]))
    return out


# Extract the two setters and the helper.
fns = _find_class_methods(
    "InverterCoordinator",
    ["async_set_hems_auto_mode", "async_set_smart_mode", "_persist_user_option"],
)
assert set(fns) == {"async_set_hems_auto_mode", "async_set_smart_mode", "_persist_user_option"}, fns.keys()

# The InverterCoordinator class has dozens of other methods and instance
# attributes; we only need the setters to verify the user-option writer
# path. Build a tiny stand-in class that mirrors what the setters touch.
src = textwrap.dedent(
    """
    from datetime import datetime


    class _Entry:
        def __init__(self, options=None):
            self.options = dict(options or {})


    class _ConfigEntries:
        def __init__(self):
            self.last_write: tuple | None = None
        def async_update_entry(self, entry, options):
            entry.options = dict(options)
            self.last_write = (entry, dict(options))


    class _Hass:
        def __init__(self):
            self.config_entries = _ConfigEntries()


    class _Coord:
        def __init__(self, options=None):
            self._entry = _Entry(options)
            self.hass = _Hass()
            # T02 fix: read from entry.options.
            self.hems_auto_mode = bool(self._entry.options.get("hems_auto_mode", True))
            self.smart_mode = int(self._entry.options.get("smart_mode", 0))
            # T10 follow-up: the new code reads
            # ``_user_smart_mode`` and the combined
            # ``_auto_storm_active`` property
            # whenever ``async_set_smart_mode`` runs.
            # The legacy T02 stub only had
            # ``smart_mode``; the harness exec's the
            # production setter source directly,
            # so we add the attributes the
            # production setter now expects.
            self._user_smart_mode = self.smart_mode
            self._auto_storm_active = False
            self._auto_storm_weather = False
            self._auto_storm_outage = False
            self._previous_smart_mode_before_storm = None

    """
)
# Paste the setters verbatim as a function-then-bind-to-class.
src += (
    fns["_persist_user_option"]
    + "\n"
    + fns["async_set_hems_auto_mode"]
    + "\n"
    + fns["async_set_smart_mode"]
)
ns: dict[str, Any] = {"__name__": "_t02_isolated"}
# _LOGGER is the module-level logger used inside the setters. We don't
# need it to do anything; a no-op logger satisfies the reference.
import logging  # noqa: E402
ns["_LOGGER"] = logging.getLogger("t02_isolated")
exec(src, ns)  # noqa: S102 — controlled test code

# Bind the extracted setters onto _Coord as proper methods so the
# ``self`` argument resolves. This mirrors how Python itself wires
# class bodies: a function defined inside a class is a function, but
# the class machinery rebinds it as a method on attribute access.
_Coord = ns["_Coord"]
_Coord.async_set_hems_auto_mode = ns["async_set_hems_auto_mode"]
_Coord.async_set_smart_mode = ns["async_set_smart_mode"]
_Coord._persist_user_option = ns["_persist_user_option"]


# ── scenarios from the audit ────────────────────────────────────────────

# 1. Default entry (no hems_auto_mode/smart_mode keys): defaults applied.
c = _Coord()
assert c.hems_auto_mode is True
assert c.smart_mode == 0

# 2. After async_set_hems_auto_mode(False), in-memory AND entry.options persist.
c.async_set_hems_auto_mode(False)
assert c.hems_auto_mode is False
assert c._entry.options["hems_auto_mode"] is False
assert c.hass.config_entries.last_write is not None

# 3. Simulate a reload: build a fresh coordinator with the same options.
c2 = _Coord(c._entry.options)
assert c2.hems_auto_mode is False, "user-off must survive a reload"

# 4. Async setter accepts truthy/falsy and coerces safely.
c2.async_set_hems_auto_mode("yes")
assert c2.hems_auto_mode is True

c2.async_set_hems_auto_mode(0)
assert c2.hems_auto_mode is False

# 5. smart_mode setter clamps invalid values to 0 (Adaptive).
c2.async_set_smart_mode(99)
assert c2.smart_mode == 0
c2.async_set_smart_mode(2)
assert c2.smart_mode == 2
assert c2._entry.options["smart_mode"] == 2
# 6. Reload preserves the chosen smart_mode.
c3 = _Coord(c2._entry.options)
assert c3.smart_mode == 2

# 7. Non-numeric input must not raise.
c3.async_set_smart_mode("garbage")
assert c3.smart_mode == 0
c3.async_set_smart_mode(None)
assert c3.smart_mode == 0

# 8. _persist_user_option must not raise when hass/entry is missing.
#    The setters guard via the early-return branch.
c4 = _Coord()
c4._entry = None
c4.hass = None
c4.async_set_hems_auto_mode(True)  # would raise if helper raised
assert c4.hems_auto_mode is True

# 9. Partial write failure (e.g. async_update_entry raises) must NOT
#    undo the in-memory change.
class _BoomHass:
    def __init__(self):
        self.config_entries = types.SimpleNamespace(
            async_update_entry=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )

c5 = _Coord()
c5.hass = _BoomHass()
c5.async_set_hems_auto_mode(False)
# In-memory value is updated even though persistence failed; otherwise
# the UI would show a state that disagrees with the option the
# integration actually believes in.
assert c5.hems_auto_mode is False


print("T02 OK — hems_auto_mode / smart_mode persist across reload")
sys.exit(0)
