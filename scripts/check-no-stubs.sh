#!/usr/bin/env bash
# CI release gate: fails if any stub exit-code-4 paths remain.
# Stubs are marked with "# STUB" comment on the return line.
set -euo pipefail

if grep -rn 'return 4.*# STUB' gdoc/ --include='*.py'; then
    echo "FAIL: stub exit code 4 found — all stubs must be replaced before release"
    exit 1
fi
echo "OK: no stubs found"
exit 0
