#!/bin/bash
# Open an installed SimpleCAD's real window under Xvfb for 30 s.
# Passes only if it is still running then (timeout's exit code 124): a build
# whose window cannot start (no GLX, no Qt platform plugin) aborts long before.
# --self-test runs headless and cannot catch that. Usage: window_smoke.sh NAME COMMAND...
set -u
name="$1"; shift
log="${TMPDIR:-/tmp}/window-${name}.log"
xvfb-run -a -s "-screen 0 1280x800x24" timeout 30 "$@" > "$log" 2>&1
status=$?
if [ "$status" != 124 ] || grep -qE "Could not initialize GLX|Fatal Python error|failed to start" "$log"; then
  echo "::error::${name}: the window did not stay up (exit ${status})"
  tail -n 25 "$log"
  exit 1
fi
echo "window stayed up 30 s: ${name}"
