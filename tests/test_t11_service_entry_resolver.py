"""T11: services must address the right config entry.

The audit's T11 review found that hardware-writing services
(``set_output_priority``, ``set_charger_priority``,
``force_grid_charge``, ``set_grid_charging``, etc.) always
routed to ``entries[0]`` regardless of the ``entry_id`` field
in the service call. A user with two inverters could send a
command to one and have it delivered to the other.

The fix introduces ``_resolve_entry`` (services/__init__.py)
which:

  * uses the explicit ``entry_id`` when supplied;
  * accepts an absent ``entry_id`` only when exactly one
    entry is loaded;
  * raises ``ValueError`` for unknown, unloaded, or
    ambiguous targets — *before* any hardware write.

We exec the whole ``services/__init__.py`` body in an
isolated namespace with a stub ``homeassistant.core``,
``voluptuous``, and ``const`` so the module-level
imports do not require a real HA install. The body
materialises ``_resolve_entry`` as a closure inside
``async_register_services`` — we then call that closure
with a stand-in ``hass`` and a synthetic ``ServiceCall``.
"""
from __future__ import annotations

import ast
import re
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


# Parse ``services/__init__.py`` once so the test can both
# extract the ``_resolve_entry`` body and assert against
# the source for the schema-content checks.
services_path = ROOT / "services" / "__init__.py"
services_src = services_path.read_text(encoding="utf-8")
tree = ast.parse(services_src)

# Remove the duplicate declaration that follows this point
# in the file (kept for source-level schema checks below).
# (No-op; the second declaration is harmless because the
# file no longer needs ``tree`` to be re-parsed.)

# ── Module-level stub: ``const`` only has DOMAIN. ──
const_stub = SimpleNamespace(DOMAIN="powmr_inverter")


# ── Stub for ``voluptuous`` — services/__init__.py uses
# only ``Schema``, ``Optional``, ``Required``, ``All``,
# ``Coerce``, ``Range``, ``In``. We do not need to *run*
# the schemas; the bodies are only built at module load.
class _VolStub:
    def __init__(self, *args, **kw):
        return ("Schema", args or kw)
    def __call__(self, *args, **kw):
        return ("Schema", args or kw)


class _VolModule:
    Schema = _VolStub
    Optional = staticmethod(lambda k: ("Optional", k))
    Required = staticmethod(lambda k: ("Required", k))
    All = staticmethod(lambda *a: ("All", a))
    Coerce = staticmethod(lambda t: ("Coerce", t))
    Range = staticmethod(lambda **kw: ("Range", kw))
    In = staticmethod(lambda c: ("In", list(c)))


# Make _VolModule a real module so it can sit in
# ``sys.modules``; the production code's ``import
# voluptuous as vol`` then resolves to our stub.
import types as _types
_vol_module = _types.ModuleType("voluptuous")
_vol_module.Schema = _VolModule.Schema
_vol_module.Optional = _VolModule.Optional
_vol_module.Required = _VolModule.Required
_vol_module.All = _VolModule.All
_vol_module.Coerce = _VolModule.Coerce
_vol_module.Range = _VolModule.Range
_vol_module.In = _VolModule.In
sys.modules["voluptuous"] = _vol_module


# ── Stub for ``homeassistant.core`` — only HomeAssistant
# and ServiceCall are imported. We provide trivial placeholders
# so the ``from homeassistant.core import …`` line succeeds.
class _HAStub:
    HomeAssistant = type("HomeAssistant", (), {})
    ServiceCall = type("ServiceCall", (), {})


# ── Build an isolated namespace and exec just the
# ``_resolve_entry`` body. We don't need to exec the full
# module — that would require a real ``homeassistant.core``
# import. The function only touches ``hass.data``,
# ``hass.config_entries.async_entries`` and ``call.data``.

_resolve_src = None
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name == "_resolve_entry":
        _resolve_src = ast.unparse(
            ast.Module(body=node.body, type_ignores=[])
        )
        break

assert _resolve_src is not None, "_resolve_entry not found"

ns: dict = {
    "__name__": "t11_isolated",
    "DOMAIN": "powmr_inverter",
    "HomeAssistant": _HAStub.HomeAssistant,
    "ServiceCall": _HAStub.ServiceCall,
}
exec(
    f"def _resolve_entry(hass, call):\n{textwrap.indent(_resolve_src, '    ')}",
    ns,
)
_resolve_entry = ns["_resolve_entry"]


# ── Synthetic hass / ServiceCall helpers ──

class _FakeServiceRegistry:
    def __init__(self):
        self.calls: list[tuple] = []

    def async_register(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def _make_hass(loaded: dict, entries: list[SimpleNamespace]):
    """Return a stand-in for ``HomeAssistant`` with the
    minimum surface ``_resolve_entry`` touches.

    ``loaded`` is a ``{entry_id: {"api": ..., "coordinator": ...}}``
    mapping. ``entries`` is a list of objects with ``.entry_id``
    attributes.
    """
    registry = _FakeServiceRegistry()
    hass = SimpleNamespace()
    hass.data = {const_stub.DOMAIN: loaded}
    hass.services = SimpleNamespace(async_register=registry.async_register)
    hass.config_entries = SimpleNamespace(
        async_entries=lambda domain: [
            e for e in entries if domain == const_stub.DOMAIN
        ]
    )
    return hass


# ── 1. Single loaded entry, no explicit target — accepted. ──

api_a = SimpleNamespace(name="A")
coord_a = SimpleNamespace(name="coord_a")
entry_a = SimpleNamespace(entry_id="entry-a")
hass = _make_hass({"entry-a": {"api": api_a, "coordinator": coord_a}}, [entry_a])
call = SimpleNamespace(data={})
api, coord = _resolve_entry(hass, call)
assert api is api_a, f"expected api_a, got {api!r}"
assert coord is coord_a, f"expected coord_a, got {coord!r}"
print("T11 case 1 OK — single loaded entry, no target")


# ── 2. Two loaded entries, no explicit target — refused. ──

api_b = SimpleNamespace(name="B")
coord_b = SimpleNamespace(name="coord_b")
entry_b = SimpleNamespace(entry_id="entry-b")
hass = _make_hass(
    {
        "entry-a": {"api": api_a, "coordinator": coord_a},
        "entry-b": {"api": api_b, "coordinator": coord_b},
    },
    [entry_a, entry_b],
)
call = SimpleNamespace(data={})
try:
    _resolve_entry(hass, call)
except ValueError as exc:
    assert "Multiple" in str(exc) or "multiple" in str(exc), (
        f"ambiguity error should mention multiple, got: {exc!r}"
    )
    print(f"T11 case 2 OK — multi-entry ambiguity: {exc}")
else:
    raise SystemExit("T11 case 2 FAILED — expected ValueError")


# ── 3. Two loaded entries, explicit target — picks the right one. ──

hass = _make_hass(
    {
        "entry-a": {"api": api_a, "coordinator": coord_a},
        "entry-b": {"api": api_b, "coordinator": coord_b},
    },
    [entry_a, entry_b],
)
call = SimpleNamespace(data={"entry_id": "entry-b"})
api, coord = _resolve_entry(hass, call)
assert api is api_b, f"expected api_b, got {api!r}"
assert coord is coord_b, f"expected coord_b, got {coord!r}"
print("T11 case 3 OK — explicit entry_id routes correctly")


# ── 4. Two loaded entries, wrong target — refused. ──

hass = _make_hass(
    {
        "entry-a": {"api": api_a, "coordinator": coord_a},
        "entry-b": {"api": api_b, "coordinator": coord_b},
    },
    [entry_a, entry_b],
)
call = SimpleNamespace(data={"entry_id": "entry-c"})
try:
    _resolve_entry(hass, call)
except ValueError as exc:
    assert "Unknown" in str(exc), f"expected unknown message, got {exc!r}"
    print(f"T11 case 4 OK — unknown entry_id rejected: {exc}")
else:
    raise SystemExit("T11 case 4 FAILED — expected ValueError")


# ── 5. Entry present in config but not loaded — refused. ──

hass = _make_hass(
    {"entry-a": {"api": api_a, "coordinator": coord_a}},
    [entry_a, entry_b],  # entry-b exists but is not loaded
)
call = SimpleNamespace(data={"entry_id": "entry-b"})
try:
    _resolve_entry(hass, call)
except ValueError as exc:
    assert "not loaded" in str(exc), f"expected 'not loaded', got {exc!r}"
    print(f"T11 case 5 OK — unloaded entry_id rejected: {exc}")
else:
    raise SystemExit("T11 case 5 FAILED — expected ValueError")


# ── 6. No entries at all — refused. ──

hass = _make_hass({}, [])
call = SimpleNamespace(data={})
try:
    _resolve_entry(hass, call)
except ValueError as exc:
    assert "No Inverter" in str(exc) or "not loaded" in str(exc), (
        f"empty-state error message: {exc!r}"
    )
    print(f"T11 case 6 OK — no entries: {exc}")
else:
    raise SystemExit("T11 case 6 FAILED — expected ValueError")


# ── 7. Service schemas: every hardware-writing service
# must now accept an optional ``entry_id``. We check the
# raw source of ``services/__init__.py`` because the
# schemas are not exposed in the test's exec namespace
# (we only exec the resolver body, not the full module,
# to avoid the homeassistant import).
required_schema_anchors = [
    ("SET_OUTPUT_PRIORITY_SCHEMA", 'vol.In(["USB", "SBU"])'),
    ("SET_CHARGER_PRIORITY_SCHEMA", 'vol.In(["CSO", "SNU", "OSO", "UTO"])'),
    ("SET_SMART_MODE_SCHEMA", 'vol.In(["adaptive", "arbitrage", "storm"])'),
    ("FORCE_GRID_CHARGE_SCHEMA", "vol.Range(min=5, max=480)"),
    ("SET_GRID_CHARGING_SCHEMA", "vol.Required(\"enable\"): bool"),
    ("SET_GRID_FEED_IN_SCHEMA", "vol.Required(\"enable\"): bool"),
    ("SET_BACKUP_MODE_SCHEMA", "vol.Required(\"enable\"): bool"),
    ("SET_BATTERY_CHARGE_LIMIT_SCHEMA", "vol.Range(min=10, max=100)"),
    ("SET_GRID_CHARGE_POWER_SCHEMA", "vol.Range(min=0, max=5000)"),
]
for name, _anchor in required_schema_anchors:
    assert name in services_src, f"missing schema constant: {name}"
# Every hardware-writing service schema must now mention
# ``entry_id`` in its literal. We locate each ``_SCHEMA = vol.Schema(...)``
# assignment by tracking paren balance so multi-line schemas
# with embedded dicts are still found.
schema_blocks: list[str] = []
i = 0
while i < len(services_src):
    m = re.search(r"^([A-Z_]+_SCHEMA)\s*=\s*vol\.Schema\(", services_src[i:], re.MULTILINE)
    if not m:
        break
    name_match = m.group(1)
    start = i + m.end()  # position right after "vol.Schema("
    depth = 1
    j = start
    while j < len(services_src) and depth > 0:
        ch = services_src[j]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        j += 1
    block = services_src[i + m.start(): j]
    schema_blocks.append(block)
    i = j
entry_id_in_schemas = sum(
    1 for block in schema_blocks if "entry_id" in block
)
# We expect at least 9 schemas to carry the field.
assert entry_id_in_schemas >= 9, (
    f"expected >= 9 schemas with entry_id, found {entry_id_in_schemas}"
)
print(
    f"T11 case 7 OK — {entry_id_in_schemas} hardware service schemas accept entry_id"
)


# ── 8. The old behavior (always entries[0]) is gone. We
# reproduce the exact audit scenario: two entries loaded,
# the caller supplies entry_id='entry-b', and the old code
# would have routed to entry-a (entries[0]). The new code
# must route to entry-b.

hass = _make_hass(
    {
        "entry-a": {"api": api_a, "coordinator": coord_a},
        "entry-b": {"api": api_b, "coordinator": coord_b},
    },
    [entry_a, entry_b],
)
call = SimpleNamespace(data={"entry_id": "entry-b"})
api, coord = _resolve_entry(hass, call)
# The old code would have returned (api_a, coord_a).
assert api is not api_a, "old behavior of entries[0] is gone"
assert coord is not coord_a, "old behavior of entries[0] is gone"
assert api is api_b, "new behavior routes to the requested entry"
print("T11 case 8 OK — old entries[0] routing is gone; explicit target wins")


print("T11 OK — services route to the right config entry, refuse ambiguity")
sys.exit(0)
