#!/bin/bash
# R06 follow-up: verify the test runner
# reports non-zero exit when a JS suite
# fails. We run with a deliberately
# broken ``tests/test_fail.cjs`` and
# expect the runner to exit non-zero.

set -e
cd "$(dirname "$0")/.."

# Create a deliberately failing JS
# suite.
cat > tests/test_fail.cjs <<'EOF'
throw new Error('intentional failure for runner smoke test');
EOF
trap "rm -f tests/test_fail.cjs" EXIT

set +e
# The runner should fail because
# ``test_fail.cjs`` throws.
.test-venv/bin/python tests/run_all.py --js-only
EXIT=$?
set -e

if [ "$EXIT" -eq 0 ]; then
  echo "FAIL: runner returned 0 even though a suite threw"
  exit 1
fi
echo "OK: runner correctly exited $EXIT on JS suite failure"
