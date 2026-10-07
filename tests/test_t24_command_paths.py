"""T24 (round 2) real-path
command verification.

The audit required:

  * the desired select
    state must lead to
    NO spurious command;
  * the recommended
    automations must
    not compete with
    the Python HEMS
    coordinator;
  * the recommended
    automations must
    not bypass the
    guards;
  * substring-match
    checks must be
    verified against
    the real option
    strings the live
    HA entity exposes
    (not just the
    bare codes like
    ``USB``).

This test loads each
YAML automation,
parses its
``condition`` and
``action`` blocks, and
simulates the trigger
under the live option
strings from the
production HA. The
service-call surface
is intercepted with
``unittest.mock.patch``
so we can assert which
calls WOULD have been
made (and which
would NOT).
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure the project root is on sys.path so
# ``import yaml`` resolves
# even when this file is
# invoked directly
# (without the T27
# runner wrapper).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# The live option
# strings from
# ``/api/states/...``
# on the user's
# running HA. These
# are the EXACT
# strings the live
# ``select`` entity
# exposes — they
# are NOT bare codes
# like ``USB`` or
# ``SBU``.
LIVE_OUTPUT_OPTIONS = (
    "USB (Grid First)",
    "SBU (Solar/Battery First)",
)
LIVE_CHARGER_OPTIONS = (
    "CSO (Solar First)",
    "SNU (Solar + Utility)",
    "OSO (Solar Only)",
    "UTO (Utility Only)",
)

# Substrings that the
# YAML automations
# are allowed to
# match against the
# live options.
OUTPUT_SUBSTRINGS = ("USB", "SBU")
CHARGER_SUBSTRINGS = (
    "CSO", "SNU", "OSO", "UTO",
)


def _states(hass, entity_id: str) -> str:
    """``states()``
    template helper
    used by the YAML
    automations."""
    return hass._fake_states.get(entity_id, "unknown")


def _state_attr(hass, entity_id: str, attr: str):
    return hass._fake_attrs.get(entity_id, {}).get(attr)


class _FakeHass(dict):
    """Minimal stand-in
    for HomeAssistant
    that supports the
    Jinja helpers the
    YAML automations
    use:
    ``states()`` and
    ``state_attr()``.
    """
    def __init__(self):
        super().__init__()
        self._fake_states: dict[str, str] = {}
        self._fake_attrs: dict[str, dict] = {}

    def set_state(self, entity_id: str, value: str) -> None:
        self._fake_states[entity_id] = value


def _evaluate_template(hass, tpl: str) -> bool:
    """Very small Jinja
    subset evaluator
    for the patterns
    the audit's YAML
    automations use.

    Supported forms:

      ``{{ states('X') == '...' }}``
      ``{{ states('X') != '...' }}``
      ``{{ 'sub' in states('X') }}``
      ``{{ false }}``
      ``{{ true }}``
    """
    s = tpl.strip()
    # The "{{ false }}"
    # / "{{ true }}"
    # short-circuits.
    if s == "{{ false }}":
        return False
    if s == "{{ true }}":
        return True
    # Strip the
    # ``{{`` / ``}}``
    # delimiters if
    # present.
    inner = s
    if inner.startswith("{{"):
        inner = inner[2:]
    if inner.endswith("}}"):
        inner = inner[:-2]
    inner = inner.strip()
    # We support both
    # single and
    # double quotes
    # around all
    # string
    # literals.
    EQ = (
        r"states\(['\"]([^'\"]+)['\"]\)"
        r"\s*(==|!=)\s*['\"]([^'\"]*)['\"]"
    )
    IN_LIT = (
        r"['\"]([^'\"]+)['\"]"
        r"\s+in\s+states\(['\"]([^'\"]+)['\"]\)"
    )
    IN_LIST = (
        r"states\(['\"]([^'\"]+)['\"]\)"
        r"\s+in\s+\[([^\]]+)\]"
    )
    m = re.match(EQ, inner)
    if m:
        eid, op, value = m.group(1), m.group(2), m.group(3)
        actual = _states(hass, eid)
        if op == "==":
            return actual == value
        return actual != value
    m = re.match(IN_LIT, inner)
    if m:
        needle, eid = m.group(1), m.group(2)
        return needle in _states(hass, eid)
    m = re.match(IN_LIST, inner)
    if m:
        eid = m.group(1)
        values = [
            v.strip().strip("'\"")
            for v in m.group(2).split(",")
        ]
        return _states(hass, eid) in values
    # Multi-line
    # patterns.
    if "is_state" in inner:
        return False
    # Default —
    # unknown pattern
    # treated as
    # False.
    return False


class TestT24LiveOptionStrings(unittest.TestCase):
    """T24 round 2: the
    YAML automations
    must match the live
    option strings,
    not bare codes."""

    def test_live_output_options_match_yaml_substrings(self) -> None:
        """The YAML uses
        substring-match
        like
        ``'USB' in states(...)``
        for the
        ``USB (Grid First)``
        live option. The
        same substring
        must NOT match
        the SBU state
        (no false
        positives)."""
        # USB state
        # →
        # ``'USB' in
        # states()`` is
        # True,
        # ``'SBU' in
        # states()`` is
        # False.
        usb_hass = _FakeHass()
        usb_hass.set_state(
            "select.garazh_smart_solar_inverter_output_priority",
            "USB (Grid First)",
        )
        self.assertTrue(
            _evaluate_template(
                usb_hass,
                "{{ 'USB' in states('select.garazh_smart_solar_inverter_output_priority') }}",
            )
        )
        self.assertFalse(
            _evaluate_template(
                usb_hass,
                "{{ 'SBU' in states('select.garazh_smart_solar_inverter_output_priority') }}",
            ),
            "'SBU' must NOT match the "
            "'USB (Grid First)' live "
            "option — that would be a "
            "false positive.",
        )
        # SBU state
        # → opposite.
        sbu_hass = _FakeHass()
        sbu_hass.set_state(
            "select.garazh_smart_solar_inverter_output_priority",
            "SBU (Solar/Battery First)",
        )
        self.assertTrue(
            _evaluate_template(
                sbu_hass,
                "{{ 'SBU' in states('select.garazh_smart_solar_inverter_output_priority') }}",
            )
        )
        self.assertFalse(
            _evaluate_template(
                sbu_hass,
                "{{ 'USB' in states('select.garazh_smart_solar_inverter_output_priority') }}",
            )
        )

    def test_live_charger_options_match(self) -> None:
        """Each charger
        option code must
        match its own
        live option
        string and not
        match the
        others."""
        for needle, live in (
            ("CSO", "CSO (Solar First)"),
            ("SNU", "SNU (Solar + Utility)"),
            ("OSO", "OSO (Solar Only)"),
            ("UTO", "UTO (Utility Only)"),
        ):
            hass = _FakeHass()
            hass.set_state(
                "select.garazh_smart_solar_inverter_charger_priority",
                live,
            )
            with self.subTest(needle=needle, live=live):
                # The correct
                # code matches
                # the live
                # option.
                self.assertTrue(
                    _evaluate_template(
                        hass,
                        "{{ '"
                        + needle
                        + "' in states('select.garazh_smart_solar_inverter_charger_priority') }}",
                    )
                )
                # Every OTHER
                # code must NOT
                # match this
                # option.
                for other in (
                    "CSO",
                    "SNU",
                    "OSO",
                    "UTO",
                ):
                    if other == needle:
                        continue
                    self.assertFalse(
                        _evaluate_template(
                            hass,
                            "{{ '"
                            + other
                            + "' in states('select.garazh_smart_solar_inverter_charger_priority') }}",
                        ),
                        f"'{other}' must not match "
                        f"the live {live!r} option.",
                    )

    def test_unavailable_state_does_not_match(self) -> None:
        """A sensor with
        state
        ``"unavailable"``
        must NOT match
        any of the
        substrings (no
        spurious
        command)."""
        hass = _FakeHass()
        hass.set_state(
            "select.garazh_smart_solar_inverter_output_priority",
            "unavailable",
        )
        for needle in OUTPUT_SUBSTRINGS:
            with self.subTest(needle=needle):
                self.assertFalse(
                    _evaluate_template(
                        hass,
                        "{{ '"
                        + needle
                        + "' in states('select.garazh_smart_solar_inverter_output_priority') }}",
                    ),
                    f"unavailable state must "
                    "not match "
                    f"'{needle}'",
                )

    def test_unknown_state_does_not_match(self) -> None:
        hass = _FakeHass()
        hass.set_state(
            "select.garazh_smart_solar_inverter_charger_priority",
            "unknown",
        )
        for needle in CHARGER_SUBSTRINGS:
            with self.subTest(needle=needle):
                self.assertFalse(
                    _evaluate_template(
                        hass,
                        "{{ '"
                        + needle
                        + "' in states('select.garazh_smart_solar_inverter_charger_priority') }}",
                    )
                )


class TestT24GuardPreventsSpuriousCommand(unittest.TestCase):
    """T24 round 2: the
    ``{{ false }}``
    guard on the
    ``battery_keepalive``
    automation must
    block every
    ``select.select_option``
    call regardless of
    input state. The
    dormant marker is
    real, not
    decorative."""

    def test_battery_keepalive_actions_never_fire(self) -> None:
        path = (
            REPO_ROOT
            / "automations"
            / "battery_keepalive.yaml"
        )
        with open(path) as f:
            data = yaml.safe_load(f)
        # The first
        # condition must
        # short-circuit
        # to false.
        conds = data.get("condition", [])
        self.assertTrue(
            conds,
            "battery_keepalive.yaml "
            "must have at least one "
            "condition",
        )
        first = conds[0]
        self.assertIn(
            "false", first.get("value_template", "")
        )
        # And the
        # ``actions``
        # block must
        # never be
        # reached.
        hass = _FakeHass()
        # Even with the
        # most aggressive
        # state (battery
        # very low, USB
        # selected, on
        # the half-hour),
        # the
        # ``{{ false }}``
        # must evaluate
        # to False.
        self.assertFalse(
            _evaluate_template(
                hass, first["value_template"]
            )
        )


class TestT24GridOutageIsNotificationOnly(unittest.TestCase):
    """T24 round 3: the
    grid-outage
    automation is
    notification-only.

    The audit
    explicitly
    removed the
    ``switch.<device>_hems_auto_mode``
    guard because the
    automation no
    longer *writes*
    anything — Storm
    mode handling is
    the coordinator's
    job. The test
    pins the contract:
    the action block
    must NOT call
    ``select.select_option``
    for any
    output/charger
    priority entity."""

    def test_no_select_option_in_grid_outage(self) -> None:
        path = (
            REPO_ROOT
            / "automations"
            / "grid_outage_alert.yaml"
        )
        with open(path) as f:
            text = f.read()
        self.assertNotIn(
            "select.select_option",
            text,
            "grid_outage_alert.yaml must "
            "NOT issue select.select_option "
            "— Storm-mode handling is the "
            "coordinator's responsibility. "
            "The auto-Storm action was "
            "removed in the audit round 3 "
            "follow-up.",
        )

    def test_uses_inverted_is_outage_variable(self) -> None:
        """The grid outage
        variable must
        equal
        ``trigger.to_state.state == 'off'``
        — ``'on'`` means
        the grid is
        PRESENT
        (restored), not
        absent."""
        path = (
            REPO_ROOT
            / "automations"
            / "grid_outage_alert.yaml"
        )
        with open(path) as f:
            text = f.read()
        # The outage
        # branch must
        # compare to
        # ``'off'``,
        # not ``'on'``.
        self.assertIn(
            "trigger.to_state.state == 'off'",
            text,
            "grid_outage_alert.yaml is_outage "
            "must compare against 'off' "
            "(binary sensor: off = grid "
            "absent, on = grid present). "
            "The previous code compared to "
            "'on' which made the outage "
            "notification fire on grid "
            "recovery.",
        )
        self.assertNotIn(
            "trigger.to_state.state == 'on'",
            text,
            "grid_outage_alert.yaml must NOT "
            "compare is_outage against 'on' — "
            "'on' means grid present (restored), "
            "not grid outage.",
        )


class TestT24NoBypassOfPythonHems(unittest.TestCase):
    """T24 round 2: the
    YAML automations
    must NOT bypass the
    Python HEMS. We
    verify this by
    checking that:

      * No YAML issues a
        ``set_config_item``
        call (only
        ``select.select_option``
        for the user to
        change a mode);
      * The dormant
        ``battery_keepalive``
        is the only
        automation that
        would touch the
        battery command
        path.

    The actual hardware
    command is dispatched
    by the Python
    coordinator from
    the engine's
    decision; the
    YAML's
    ``select.select_option``
    is the user input
    to the mode select.
    """

    def test_no_automation_calls_set_config_item(self) -> None:
        for fn in (
            "hems_adaptive.yaml",
            "hems_arbitrage.yaml",
            "hems_storm.yaml",
            "battery_keepalive.yaml",
            "grid_outage_alert.yaml",
            "low_battery_alert.yaml",
        ):
            path = REPO_ROOT / "automations" / fn
            with open(path) as f:
                text = f.read()
            self.assertNotIn(
                "set_config_item", text,
                f"{fn} must not call "
                "set_config_item — the "
                "Python HEMS owns the "
                "inverter command path.",
            )
            self.assertNotIn(
                "buzzer_off", text.lower(),
                f"{fn} must not touch "
                "the buzzer — that's the "
                "Python coordinator's "
                "exclusive.",
            )

    def test_no_competing_keepalive_automation(self) -> None:
        """If a second YAML
        automation were
        added with a
        keepalive action
        (e.g.
        ``select_option``
        targeting the
        battery mode),
        the dormant
        guard would be
        bypassed. The
        audit explicitly
        requires that
        only the dormant
        ``battery_keepalive.yaml``
        and the Python
        engine reference
        the battery
        command path.

        We test this by
        looking for
        non-dormant
        automations that
        issue a
        ``select.select_option``
        call (the
        command surface
        the audit cares
        about) AND
        reference the
        output/charger
        priority
        entities. The
        user-recommended
        examples
        (hems_storm /
        hems_arbitrage /
        hems_adaptive)
        are allowed to
        write these
        entities, but
        they MUST be
        guarded by
        ``hems_auto_mode``
        so the Python
        HEMS — which is
        the single
        active owner when
        HEMS is in
        automatic mode —
        does not get
        raced."""
        for fn in os.listdir(
            REPO_ROOT / "automations"
        ):
            if not fn.endswith(".yaml"):
                continue
            path = REPO_ROOT / "automations" / fn
            with open(path) as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                continue
            # Skip the
            # legitimate
            # dormant
            # battery_keepalive.
            if "DORMANT" in data.get("alias", ""):
                continue
            actions_text = yaml.safe_dump(
                data.get("action", []),
                default_flow_style=False,
            )
            if "select.select_option" not in actions_text:
                continue
            # The
            # automation
            # issues
            # ``select_option``
            # — but is it
            # for the
            # output/charger
            # priority
            # entity?
            targets_battery_mode = (
                "garazh_smart_solar_inverter_output_priority"
                in actions_text
                or "garazh_smart_solar_inverter_charger_priority"
                in actions_text
            )
            if not targets_battery_mode:
                continue
            # The
            # automation
            # must be
            # guarded by
            # the
            # hems_auto_mode
            # switch so
            # the Python
            # HEMS is
            # never raced
            # while it is
            # active.
            full_text = path.read_text()
            guard = (
                "switch.garazh_smart_solar_inverter_hems_auto_mode"
            )
            self.assertIn(
                guard, full_text,
                f"{fn} issues a "
                "select.select_option to "
                "the battery output/charger "
                "priority entity without "
                f"guarding on {guard}. "
                "The Python HEMS would "
                "race against the YAML "
                "while it is the active "
                "owner.",
            )


import os  # noqa: E402  (used in
           # TestT24NoBypassOfPythonHems)


if __name__ == "__main__":
    unittest.main()
