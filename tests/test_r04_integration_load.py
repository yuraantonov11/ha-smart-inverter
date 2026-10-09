"""R04: verify ``coordinator.py`` uses package-relative
imports that resolve when the integration is loaded
by HA as ``custom_components.powmr_inverter``.

The previous deploy commit ``49b43c1`` used absolute
imports (``from hems.engine import _finite_number``)
at three sites in ``coordinator.py``. Because
``coordinator.py`` is loaded as
``custom_components.powmr_inverter.coordinator``, those
absolute imports raised
``ImportError: cannot import name '_finite_number'``
and the integration fell into the ``unavailable`` state
on first ``ha core restart``.

This test is a *static* check on the production
source — the dynamic import check requires the real
HA runtime, which we verify directly on the live
host. The static check is the regression guard:
if someone re-introduces an absolute import of
``hems.engine`` in ``coordinator.py``, this test
fails.
"""

from __future__ import annotations

import pathlib
import re
import unittest


class RelativeImport(unittest.TestCase):
    """``coordinator.py`` must use ``from .hems.engine
    import _finite_number`` and the import must resolve
    when the integration is loaded as
    ``custom_components.powmr_inverter``.
    """

    def setUp(self) -> None:
        self.integration_root = (
            pathlib.Path(__file__).resolve().parent.parent
        )
        # Sanity: the file exists and is a file.
        self.assertTrue(
            (self.integration_root / "coordinator.py").is_file(),
            "coordinator.py not found at the expected path",
        )

    def test_coordinator_uses_only_relative_hems_engine_import(self):
        """Static check: the production ``coordinator.py``
        must not have any absolute ``from hems.engine``
        imports (the bug). Only ``from .hems.engine``.
        """
        src = (self.integration_root / "coordinator.py").read_text(
            encoding="utf-8"
        )
        abs_imp = re.findall(r"^from hems\.engine import", src, re.M)
        self.assertEqual(
            abs_imp, [],
            f"coordinator.py still has absolute imports "
            f"of hems.engine: {abs_imp}",
        )
        rel_imp = re.findall(
            r"^from \.hems\.engine import", src, re.M
        )
        self.assertGreaterEqual(
            len(rel_imp), 1,
            "coordinator.py is missing the relative "
            "import of _finite_number from .hems.engine",
        )

    def test_coordinator_no_module_level_duplicate_imports(self):
        """Static check: there should be exactly ONE
        module-level import of ``_finite_number`` in
        ``coordinator.py``. Multiple imports of the
        same symbol (even all relative) is a smell
        that we accidentally re-introduced the bug.
        """
        src = (self.integration_root / "coordinator.py").read_text(
            encoding="utf-8"
        )
        # The import is parenthesised on multiple
        # lines: ``from .hems.engine import (\n
        #     _finite_number,\n  ...)``. The module
        # is referenced on the line that starts with
        # ``from``, and the symbol appears on a later
        # indented line in the same statement.
        # Count module-level ``from .hems.engine import``
        # lines that introduce the symbol.
        from_lines = re.findall(
            r"^from \.hems\.engine import", src, re.M,
        )
        self.assertEqual(
            len(from_lines), 1,
            f"expected exactly one module-level "
            f"'from .hems.engine import' line, got "
            f"{len(from_lines)}:\n{from_lines}",
        )
        # And the import must include _finite_number.
        self.assertIn(
            "_finite_number", from_lines[0] + src[
                src.index(from_lines[0]):
                src.index(from_lines[0]) + 1000
            ],
            "the single 'from .hems.engine import' "
            "block does not include _finite_number",
        )


if __name__ == "__main__":
    unittest.main()
