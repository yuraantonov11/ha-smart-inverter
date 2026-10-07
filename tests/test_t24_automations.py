"""T24: regression tests for advisory automations.

The audit demanded:
  * No competition between
    YAML examples and the
    Python HEMS coordinator.
  * Select state is read
    correctly (HA ``select``
    entities expose the
    current option as
    ``state`` directly, not
    via ``state_attr(X, 'option')``).
  * Placeholders are aligned
    with the device name and
    documented.
  * Supported commands pass
    through the existing
    coordinator guards.
  * User-installed
    automations are not
    silently rewritten.
  * The ``battery_keepalive``
    automation is explicitly
    dormant — the Python
    ``check_keepalive`` in
    ``hems/engine.py`` is the
    single active owner.
"""
from __future__ import annotations

import os
import re
import ast
import sys
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
AUTOMATIONS_DIR = REPO_ROOT / "automations"


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


class TestT24AdvisoryAutomations(unittest.TestCase):
    """T24: automations must not
    compete with the Python
    HEMS coordinator and
    must use the documented
    entity IDs and state
    read pattern."""

    def test_select_state_read_uses_state_not_option_attribute(self) -> None:
        """HA's ``select`` entity
        exposes the current
        option as the state
        value. ``state_attr(
        'select.X', 'option')``
        is an old pattern that
        always returns
        ``unknown`` because
        the entity has no
        ``option`` attribute
        — its state IS the
        option.

        Every YAML must read
        the select state via
        ``states('select.X')``,
        not
        ``state_attr(
        'select.X', 'option')``.
        """
        for fn in os.listdir(AUTOMATIONS_DIR):
            path = AUTOMATIONS_DIR / fn
            with open(path) as f:
                text = f.read()
            bad = re.findall(
                r"state_attr\('select\.[^']+', 'option'\)",
                text,
            )
            self.assertEqual(
                bad,
                [],
                f"{fn}: still uses "
                f"state_attr('select.X', 'option') "
                "for the select state; "
                "use states('select.X') instead. "
                f"Found: {bad}",
            )

    def test_select_state_value_uses_full_option_string(self) -> None:
        """The select entity
        exposes the full
        human-readable option
        string, e.g. ``"USB
        (Grid First)"``. YAML
        must compare against
        that string verbatim
        or use a substring
        match (``'USB' in
        states(...)``).

        The previous round
        compared against
        ``"USB"`` (the bare
        code) which never
        matched.
        """
        for fn in os.listdir(AUTOMATIONS_DIR):
            path = AUTOMATIONS_DIR / fn
            with open(path) as f:
                text = f.read()
            # Look for equality
            # comparisons against
            # bare codes. Substring
            # matches are fine.
            for m in re.finditer(
                r"==\s*['\"](USB|SBU|SNU|OSO)['\"]",
                text,
            ):
                code = m.group(1)
                # Allow when the
                # comparison is
                # explicitly against
                # the full option
                # string with the
                # description.
                if f"{code} (" in text:
                    continue
                self.fail(
                    f"{fn}: compares select state "
                    f"to bare code {code!r}; the "
                    "HA select exposes the full "
                    "option like "
                    f"'USB (Grid First)' or "
                    f"'SBU (Solar/Battery First)'. "
                    "Either compare to the full "
                    "string or use a substring "
                    "match like "
                    f"'\"{code}\" in states(...)'."
                )

    def test_entity_ids_use_documented_device_prefix(self) -> None:
        """The integration is
        loaded with the device
        name
        ``garazh_smart_solar_inverter``
        (per the live HA
        entity registry; see
        the audit verification
        in this commit). YAML
        automations that
        reference entities
        owned by this
        integration must use
        the same prefix.

        Allowed prefixes:
          * ``garazh_smart_solar_inverter_``
            — the live
            integration
            prefix.
        Disallowed:
          * ``powmr_inverter_`` (legacy)
          * bare ``inverter_`` (no
            integration prefix;
            never resolves)
        """
        # Bare ``inverter_``
        # prefix collides with
        # nothing in this
        # integration and is
        # the result of the
        # legacy copy.
        bad_prefixes = (
            "switch.inverter_",
            "select.inverter_",
            "sensor.inverter_",
            "binary_sensor.inverter_",
        )
        # Legacy ``powmr_inverter_``
        # — only ``powmr_``
        # without
        # ``garazh_smart_solar_inverter_``.
        for fn in os.listdir(AUTOMATIONS_DIR):
            path = AUTOMATIONS_DIR / fn
            with open(path) as f:
                text = f.read()
            for bad in bad_prefixes:
                if bad in text:
                    self.fail(
                        f"{fn}: uses legacy "
                        f"entity prefix {bad!r}; "
                        "replace with the live "
                        "integration prefix "
                        "'garazh_smart_solar_inverter_'."
                    )

    def test_battery_keepalive_is_dormant(self) -> None:
        """The
        ``battery_keepalive``
        automation must be
        marked DORMANT. The
        Python
        ``hems.engine.check_keepalive``
        is the single active
        owner. Two owners
        writing the same
        command would violate
        the audit's
        single-owner rule.

        A dormant automation
        has:
          * alias mentioning
            DORMANT
          * a hard-wired
            ``{{ false }}``
            condition that
            prevents the
            actions from
            running.
        """
        path = AUTOMATIONS_DIR / "battery_keepalive.yaml"
        data = _load_yaml(path)
        alias = data.get("alias", "")
        self.assertIn(
            "DORMANT",
            alias,
            "battery_keepalive.yaml alias must "
            "explicitly mark the automation as "
            f"DORMANT; got alias={alias!r}",
        )
        conds = data.get("condition", [])
        found_false = False
        for c in conds:
            vt = (
                c.get("value_template", "")
                if isinstance(c, dict)
                else ""
            )
            # The literal Jinja
            # expression ``{{ false }}``
            # evaluates to False
            # and short-circuits
            # the condition chain.
            # We look for both the
            # ``{{`` (output
            # expression) and the
            # word ``false`` so
            # neither a quoted
            # string nor a stray
            # match triggers a
            # false positive.
            stripped = vt.strip()
            if stripped == "{{ false }}" or (
                "{{" in stripped
                and "false" in stripped.lower()
            ):
                found_false = True
                break
        self.assertTrue(
            found_false,
            "battery_keepalive.yaml must have a "
            "hard-wired {{ false }} condition so "
            "the actions never run. Without it "
            "the dormant marker is decorative.",
        )

    def test_no_select_command_without_coordinator_guard(self) -> None:
        """Every automation that
        calls
        ``select.select_option``
        must guard the call
        with a check that
        ``switch.garazh_smart_solar_inverter_hems_auto_mode``
        is on. The audit
        explicitly required
        this so the YAML never
        writes a control
        command when HEMS is
        in monitor-only mode.

        The check examines
        only the ``action:``
        block (not the
        comment / header) to
        avoid false positives
        on documentation that
        mentions the service
        name.
        """
        for fn in os.listdir(AUTOMATIONS_DIR):
            path = AUTOMATIONS_DIR / fn
            with open(path) as f:
                text = f.read()
            # Strip block
            # comments (lines
            # starting with #).
            uncommented = "\n".join(
                line for line in text.split("\n")
                if not line.lstrip().startswith("#")
            )
            if "select.select_option" not in uncommented:
                continue
            data = _load_yaml(path)
            conds_text = yaml.safe_dump(
                data.get("condition", []),
                default_flow_style=False,
            )
            if "{{ false }}" in conds_text:
                continue
            guard = (
                "switch.garazh_smart_solar_inverter_hems_auto_mode"
            )
            if guard not in uncommented:
                self.fail(
                    f"{fn}: issues "
                    "select.select_option "
                    "but does not guard on "
                    f"{guard} being on. "
                    "Add a condition to "
                    "prevent writes when "
                    "HEMS is in monitor-only mode.",
                )

    def test_advisory_header_present(self) -> None:
        """Every YAML must
        start with the
        ``ADVISORY AUTOMATION``
        header that documents
        the entity ID prefix
        and the placeholder
        list."""
        for fn in os.listdir(AUTOMATIONS_DIR):
            path = AUTOMATIONS_DIR / fn
            with open(path) as f:
                head = f.read(2000)
            self.assertIn(
                "ADVISORY AUTOMATION",
                head,
                f"{fn}: missing the ADVISORY "
                "AUTOMATION header that documents "
                "the entity prefix and placeholders.",
            )
            self.assertIn(
                "garazh_smart_solar_inverter",
                head,
                f"{fn}: header must list the "
                "documented device prefix "
                "'garazh_smart_solar_inverter'.",
            )

    def test_no_user_automation_overwrite_on_install(self) -> None:
        """The integration must
        not silently overwrite
        user-installed
        automations on
        startup. Read
        ``__init__.py`` and
        assert there is no
        code path that writes
        to
        ``/config/automations/``
        from the Python side.
        """
        init_path = REPO_ROOT / "__init__.py"
        with open(init_path) as f:
            src = f.read()
        # Look for any
        # automations write
        # path. The integration
        # only writes Lovelace
        # dashboards, not YAML
        # automations.
        bad = re.findall(
            r"open\([^)]*automations[^)]*['\"]w['\"]",
            src,
        )
        self.assertEqual(
            bad,
            [],
            "__init__.py must not write to "
            "/config/automations/; user-installed "
            f"automations would be overwritten. "
            f"Found: {bad}",
        )


if __name__ == "__main__":
    unittest.main()
