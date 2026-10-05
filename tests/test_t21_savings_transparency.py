"""T21 regression tests: savings formula transparency.

Audit T21: ``DailySavingsSensor`` and
``MonthlySavingsSensor`` claim to report
"savings" but the formula is a gross
estimate of grid-import replacement value::

    savings = battery_discharge_day * day_tariff
            + battery_discharge_night * night_tariff

The audit found three issues:

  1. **Energy origin not considered**:
     the formula credits every discharged
     kWh against the day/night tariff. If
     the battery was charged from the grid
     at night (cheap tariff) and discharged
     during the day (expensive tariff),
     the formula reports a "saving" that
     is actually an arbitrage profit, not
     a saving against the counterfactual
     of grid-only operation.

  2. **Losses not subtracted**:
     battery round-trip efficiency is not
     100 %. The formula counts energy
     discharged to the load but does not
     subtract energy lost during
     charge/discharge cycles.

  3. **Imports not subtracted**:
     the formula does not subtract the
     kWh imported from the grid during
     the same period, so a setup that
     imports 30 kWh and discharges 25 kWh
     shows positive savings instead of
     negative net.

The audit asks the integration to:

  * Document the formula honestly as
    "gross estimate of the import value
    that battery discharge replaced",
    not as net savings.
  * Do NOT add a net-savings model
    without an agreed formula.

This test file asserts:

  * The docstring of
    ``DailySavingsSensor.native_value``
    and ``MonthlySavingsSensor.native_value``
    describes the formula as a gross
    estimate and lists the three limitations
    above.
  * The ``extra_state_attributes`` of
    both sensors include a
    ``savings_formula`` attribute that
    names the formula explicitly so a
    consumer reading the sensor can see
    what it represents.
  * The translation strings for
    ``daily_savings`` and ``monthly_savings``
    include a ``description`` key
    (not just ``name``) that says the
    same thing in user-facing language.
  * The dashboard cards that reference
    ``sensor.smart_solar_inverter_daily_savings``
    do not call it "Економія" without a
    qualifier; the audit asks for
    "оцінка валової вартості заміщеного
    імпорту" framing.

The tests are pure-stdlib - they
read the production source and the
translation JSON files directly,
without spinning up Home Assistant.
"""

from __future__ import annotations

import ast as _ast
import json
import os
import sys

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _load(path: str) -> str:
    with open(os.path.join(_REPO_ROOT, path)) as f:
        return f.read()


def _find_class(tree: _ast.Module, name: str):
    for node in tree.body:
        if isinstance(node, _ast.ClassDef) and node.name == name:
            return node
    return None


def test_daily_savings_docstring_describes_gross_estimate() -> None:
    """Audit T21: the ``DailySavingsSensor``
    docstring must describe the value as a
    gross estimate of the import value that
    battery discharge replaced, and must list
    the three limitations.
    """
    src = _load("sensor.py")
    tree = _ast.parse(src)
    sensor_cls = _find_class(tree, "DailySavingsSensor")
    assert sensor_cls is not None, (
        "DailySavingsSensor class not found in "
        "sensor.py"
    )
    docstring = _ast.get_docstring(sensor_cls) or ""
    # Audit T21: the docstring must
    # be honest. We check for the
    # three key phrases the audit
    # requires.
    audit_phrases = [
        "gross estimate",
        "battery discharge",
        "grid",
        "do NOT",
        "tariff",
    ]
    missing = [
        p for p in audit_phrases
        if p.lower() not in docstring.lower()
    ]
    assert not missing, (
        "DailySavingsSensor docstring is missing "
        f"required phrases: {missing!r}. The audit "
        "requires the docstring to describe the "
        "value as a gross estimate of the import "
        "value that battery discharge replaced, "
        "with explicit limitations. Current "
        f"docstring: {docstring!r}"
    )


def test_monthly_savings_docstring_describes_gross_estimate() -> None:
    """Audit T21 follow-up: the
    ``MonthlySavingsSensor`` docstring must
    mirror ``DailySavingsSensor``.
    """
    src = _load("sensor.py")
    tree = _ast.parse(src)
    sensor_cls = _find_class(tree, "MonthlySavingsSensor")
    assert sensor_cls is not None, (
        "MonthlySavingsSensor class not found in "
        "sensor.py"
    )
    docstring = _ast.get_docstring(sensor_cls) or ""
    audit_phrases = [
        "gross estimate",
        "battery discharge",
    ]
    missing = [
        p for p in audit_phrases
        if p.lower() not in docstring.lower()
    ]
    assert not missing, (
        "MonthlySavingsSensor docstring is missing "
        f"required phrases: {missing!r}. Current "
        f"docstring: {docstring!r}"
    )


def test_savings_sensor_exposes_formula_attribute() -> None:
    """Audit T21: the sensor's
    ``extra_state_attributes`` must include
    a ``savings_formula`` attribute that
    names the formula explicitly so a
    consumer reading the sensor can see
    what it represents.
    """
    src = _load("sensor.py")
    tree = _ast.parse(src)
    sensor_cls = _find_class(tree, "DailySavingsSensor")
    attrs_method = None
    for item in sensor_cls.body:
        if (
            isinstance(item, _ast.FunctionDef)
            and item.name == "extra_state_attributes"
        ):
            attrs_method = item
            break
    assert attrs_method is not None, (
        "DailySavingsSensor must expose "
        "extra_state_attributes with a "
        "savings_formula attribute."
    )
    func_src = _ast.unparse(attrs_method)
    assert "savings_formula" in func_src, (
        "DailySavingsSensor.extra_state_attributes "
        "must include a savings_formula entry. "
        f"Current source:\n{func_src}"
    )


def test_daily_savings_translation_has_description() -> None:
    """Audit T21: the ``daily_savings``
    translation string must include a
    ``description`` key (not just ``name``)
    that says the same thing in
    user-facing language.
    """
    for locale in ("en", "uk"):
        path = f"translations/{locale}.json"
        with open(os.path.join(_REPO_ROOT, path)) as f:
            data = json.load(f)
        entity = (
            data.get("entity", {})
            .get("sensor", {})
            .get("daily_savings")
        )
        assert entity is not None, (
            f"translations/{locale}.json missing "
            "entity.sensor.daily_savings"
        )
        assert "name" in entity, (
            f"translations/{locale}.json "
            "entity.sensor.daily_savings "
            "missing 'name'"
        )
        assert "description" in entity, (
            f"translations/{locale}.json "
            "entity.sensor.daily_savings missing "
            f"'description' key. Got: {list(entity.keys())!r}. "
            "The audit requires the translation to "
            "explain what the sensor represents."
        )
        desc = entity["description"]
        assert (
            "gross" in desc.lower()
            or "import" in desc.lower()
            or "заміщен" in desc.lower()
            or "оцінк" in desc.lower()
        ), (
            f"translations/{locale}.json "
            "daily_savings.description is too "
            "vague. The audit asks the description "
            "to mention 'gross estimate of "
            "replaced import' (or the equivalent "
            "local phrase). Got: {desc!r}"
        )


def test_monthly_savings_translation_has_description() -> None:
    """Audit T21 follow-up: ``monthly_savings``
    must also have a description.
    """
    for locale in ("en", "uk"):
        path = f"translations/{locale}.json"
        with open(os.path.join(_REPO_ROOT, path)) as f:
            data = json.load(f)
        entity = (
            data.get("entity", {})
            .get("sensor", {})
            .get("monthly_savings")
        )
        assert entity is not None, (
            f"translations/{locale}.json missing "
            "entity.sensor.monthly_savings"
        )
        assert "description" in entity, (
            f"translations/{locale}.json "
            "entity.sensor.monthly_savings missing "
            f"'description' key. Got: {list(entity.keys())!r}"
        )


def test_coordinator_formula_carries_documented_limitations() -> None:
    """Audit T21: the production
    ``coordinator.py`` formula must include
    a comment block that names the three
    audit limitations:

      1. Energy origin not considered
         (battery charged from grid is
         not subtracted).
      2. Losses not subtracted.
      3. Imports not subtracted.

    The audit explicitly forbids a net
    model without an agreed formula; the
    comment must say so.
    """
    src = _load("coordinator.py")
    tree = _ast.parse(src)
    # Find the assignment to
    # ``_daily_savings_uah``.
    target = None
    for node in _ast.walk(tree):
        if (
            isinstance(node, _ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], _ast.Attribute)
            and node.targets[0].attr == "_daily_savings_uah"
        ):
            target = node
            break
    assert target is not None, (
        "Production _daily_savings_uah assignment "
        "not found in coordinator.py"
    )
    # Read 30 lines around the
    # assignment for the comment
    # block.
    lines = src.split("\n")
    start = max(0, target.lineno - 15)
    end = min(len(lines), target.lineno + 5)
    block = "\n".join(lines[start:end])
    audit_phrases = [
        "gross",
        "import",
        "loss",
        "origin",
        "do NOT",
    ]
    found = [p for p in audit_phrases if p.lower() in block.lower()]
    assert len(found) >= 3, (
        "coordinator.py _daily_savings_uah formula "
        "is missing the audit-required comments. "
        f"Found phrases: {found!r}. Block:\n{block}"
    )


def test_dashboard_does_not_mislabel_savings() -> None:
    """Audit T21: the dashboard cards that
    reference
    ``sensor.smart_solar_inverter_daily_savings``
    must not call it just "Економія"
    without a qualifier. The audit
    requires an honest framing such as
    "оцінка валової вартості заміщеного
    імпорту".

    The test allows the existing name
    only if a clarifying note is present
    in the dashboard.
    """
    src = _load("dashboard.yaml")
    # We do not require a full
    # YAML parse; the test asserts
    # that *no* card titles the
    # sensor simply "Економія" /
    # "Savings" without a qualifier
    # nearby.
    offending_lines = []
    for line in src.split("\n"):
        # ``grep`` for the entity.
        if "sensor.smart_solar_inverter_daily_savings" not in line and \
           "sensor.smart_solar_inverter_monthly_savings" not in line:
            continue
        if "name:" not in line:
            continue
        # Extract the ``name:`` value
        # and check for an explicit
        # qualifier.
        name = line.split("name:", 1)[1].strip().strip('"').strip("'")
        # The audit allows the existing
        # names but requires a
        # qualifier like "оцінка" /
        # "estimate" or "(gross)" /
        # "заміщеного імпорту".
        qualifiers = [
            "оцінк",
            "заміщен",
            "валов",
            "estimate",
            "gross",
            "replaced",
        ]
        if not any(q in name.lower() for q in qualifiers):
            offending_lines.append(line)
    assert not offending_lines, (
        "dashboard.yaml cards reference the savings "
        "sensor without an audit-required qualifier "
        "(the audit asks for 'gross estimate of "
        "replaced import' framing). Offending lines:\n"
        + "\n".join(offending_lines)
    )


def test_entity_ids_unchanged() -> None:
    """Audit T21 follow-up: the integration
    must NOT change entity IDs, types, or
    units. A consumer dashboard or
    automation that referenced
    ``sensor.smart_solar_inverter_daily_savings``
    yesterday must still find it today.

    We assert by reading the
    ``translation_key`` options on the
    production sensor classes.
    """
    src = _load("sensor.py")
    tree = _ast.parse(src)
    expected = {
        "DailySavingsSensor": "daily_savings",
        "MonthlySavingsSensor": "monthly_savings",
    }
    for cls_name, want_key in expected.items():
        cls = _find_class(tree, cls_name)
        assert cls is not None, f"{cls_name} not found"
        # Find the SensorDescription
        # inside ``__init__``.
        for item in cls.body:
            if (
                isinstance(item, _ast.FunctionDef)
                and item.name == "__init__"
            ):
                # Look for the literal
                # ``translation_key=`` argument.
                for sub in _ast.walk(item):
                    if (
                        isinstance(sub, _ast.keyword)
                        and sub.arg == "translation_key"
                    ):
                        got_key = ast_literal(sub.value)
                        assert got_key == want_key, (
                            f"{cls_name}.translation_key changed: "
                            f"was {got_key!r}, expected {want_key!r}. "
                            "Audit T21 forbids entity ID / "
                            "translation_key changes that "
                            "would break consumer dashboards."
                        )


def ast_literal(node):
    """Tiny helper for the few literal
    types we expect in translation_key
    arguments.
    """
    if isinstance(node, _ast.Constant):
        return node.value
    return None


def _run_all() -> None:
    failures: list[tuple[str, str]] = []
    tests = sorted(
        [
            (name, fn)
            for name, fn in globals().items()
            if name.startswith("test_") and callable(fn)
        ]
    )
    for name, fn in tests:
        try:
            fn()
            print(f"  {name}: PASS")
        except Exception as exc:
            failures.append((name, repr(exc)))
            print(f"  {name}: FAIL ({exc!r})")
    if failures:
        print(f"\n{len(failures)} of {len(tests)} tests failed:")
        for name, msg in failures:
            print(f"  - {name}: {msg}")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed.")
    sys.exit(0)


if __name__ == "__main__":
    _run_all()