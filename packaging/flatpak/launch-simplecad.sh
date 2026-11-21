#!/bin/sh
set -eu
application=/app/lib/simplecad/simplecad
if [ -f /app/lib/simplecad/resources/app.asar ]; then
  exec zypak-wrapper "$application" "$@"
fi
exec "$application" "$@"
