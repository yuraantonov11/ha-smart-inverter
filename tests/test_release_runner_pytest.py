"""The release runner must execute pytest-only suites, including failures."""
import importlib.util
import sys
from pathlib import Path


def test_pytest_fallback_executes_and_rejects_empty_and_failing_suites(tmp_path):
    path = Path(__file__).with_name("run_all.py")
    spec = importlib.util.spec_from_file_location("release_runner_test", path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    suite = tmp_path / "test_sample.py"
    suite.write_text("def test_success():\n    assert True\n", encoding="utf-8")
    assert runner._run_python_suite(sys.executable, suite, suite.name)[0]
    suite.write_text("def test_failure():\n    assert False\n", encoding="utf-8")
    assert not runner._run_python_suite(sys.executable, suite, suite.name)[0]
    suite.write_text("# no tests\n", encoding="utf-8")
    assert not runner._run_python_suite(sys.executable, suite, suite.name)[0]
