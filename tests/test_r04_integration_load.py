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

import importlib
import pathlib
import re
import shutil
import sys
import tempfile
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


def _build_isolated_load_path(
    integration_root: pathlib.Path,
) -> pathlib.Path:
    """Build a copy of the integration source tree
    in a fresh ``custom_components/`` layout.
    Returns the parent of ``custom_components/``
    (i.e. the directory that goes on ``sys.path``).
    """
    parent = pathlib.Path(tempfile.mkdtemp(prefix="r04_load_"))
    cc = parent / "custom_components"
    cc.mkdir()
    (cc / "__init__.py").write_text("")
    pi_pkg = cc / "powmr_inverter"
    pi_pkg.mkdir()
    # Copy the full integration source. The
    # relative-import contract is the same
    # regardless of which submodules are loaded —
    # what matters is that the import path
    # ``from .hems.engine import`` resolves.
    shutil.copytree(
        integration_root, pi_pkg, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            ".test-venv", "__pycache__", ".git",
            "tests", "node_modules",
        ),
    )
    # Drop the test venv from the copy if it
    # somehow crept in.
    venv = pi_pkg / ".test-venv"
    if venv.exists():
        shutil.rmtree(venv, ignore_errors=True)
    return parent


class DynamicPackageLoad(unittest.TestCase):
    """Dynamic load smoke test: build a fake
    ``custom_components.powmr_inverter`` package
    layout, import the coordinator, and verify
    that the relative import
    ``from .hems.engine import _finite_number``
    resolves to a real callable.

    Юра's audit: the previous static regex test
    is a regression guard but does NOT confirm
    the integration actually loads. This
    dynamic check exercises the import path that
    was broken in commit ``49b43c1``.
    """

    def setUp(self) -> None:
        self.integration_root = (
            pathlib.Path(__file__).resolve().parent.parent
        )

    def tearDown(self) -> None:
        for name in list(sys.modules):
            if name.startswith("custom_components.powmr_inverter"):
                del sys.modules[name]
            elif name == "custom_components":
                del sys.modules[name]

    def test_loads_as_custom_components_powmr_inverter(self):
        parent = _build_isolated_load_path(
            self.integration_root
        )
        try:
            sys.path.insert(0, str(parent))
            try:
                # Build the chain explicitly:
                # 1. import custom_components
                # 2. import custom_components.powmr_inverter
                # 3. import custom_components.powmr_inverter.coordinator
                #    — this is where the original
                #    ``ImportError: cannot import name
                #    '_finite_number' from 'hems.engine'``
                #    fired.
                cc = importlib.import_module("custom_components")
                pkg = importlib.import_module(
                    "custom_components.powmr_inverter"
                )
                coord = importlib.import_module(
                    "custom_components.powmr_inverter.coordinator"
                )
            except ImportError as exc:
                self.fail(
                    f"integration did not load as "
                    f"custom_components.powmr_inverter: "
                    f"{type(exc).__name__}: {exc}"
                )
            # The original failure: ``_finite_number``
            # bound to ``None`` because the
            # ``from hems.engine import`` (absolute)
            # was resolved against the top-level
            # ``hems`` package (which has no such
            # symbol). After the fix
            # (``from .hems.engine import``), the
            # symbol is a real callable.
            self.assertTrue(
                callable(getattr(coord, "_finite_number", None)),
                f"coordinator._finite_number is not "
                f"callable: got "
                f"{getattr(coord, '_finite_number', None)!r}",
            )
            # The integration package loaded
            # cleanly.
            self.assertTrue(pkg.__name__.endswith("powmr_inverter"))
            # And the custom_components namespace
            # exists.
            self.assertEqual(cc.__name__, "custom_components")
        finally:
            sys.path.remove(str(parent))
            for name in list(sys.modules):
                if name.startswith("custom_components.powmr_inverter"):
                    del sys.modules[name]
                elif name == "custom_components":
                    del sys.modules[name]
            shutil.rmtree(parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
