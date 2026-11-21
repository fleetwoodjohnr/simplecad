#!/usr/bin/env bash
# Run the legacy app, the candidate and the legacy app again from a scratch installation.
set -euo pipefail
if [[ $# != 3 ]]; then
  echo "usage: $0 <baseline.dmg> <candidate.dmg> <candidate-version>" >&2
  exit 2
fi

scratch=$(mktemp -d)
mount="$scratch/mount"
app="$scratch/SimpleCAD.app"
mounted=false
cleanup() {
  if "$mounted"; then hdiutil detach "$mount" >/dev/null; fi
  rm -rf -- "$scratch"
}
trap cleanup EXIT
export SIMPLECAD_USER_DATA="$scratch/renderer-profile"
export SIMPLECAD_LEGACY_SETTINGS="$scratch/settings.json"
export QT_QPA_PLATFORM=offscreen
cp -- "$(dirname "$0")/../app/tests/e2e/fixtures/features.scad3" "$scratch/model.scad3"
printf '{"theme":"dark"}\n' > "$SIMPLECAD_LEGACY_SETTINGS"
shasum -a 256 "$scratch/model.scad3" "$SIMPLECAD_LEGACY_SETTINGS" > "$scratch/user-data.sha256"

install() {
  hdiutil attach "$1" -readonly -mountpoint "$mount" -nobrowse >/dev/null
  mounted=true
  test -L "$mount/Applications"
  rm -rf -- "$app"
  ditto "$mount/SimpleCAD.app" "$app"
  hdiutil detach "$mount" >/dev/null
  mounted=false
}

check() {
  program="$app/Contents/MacOS/SimpleCAD"
  version=$("$program" --version)
  case "$version" in "$1"|*" $1") ;; *) echo "Wrong application version: $version" >&2; exit 1;; esac
  "$program" --self-test
  shasum -a 256 -c "$scratch/user-data.sha256"
}

check_association() {
  python - "$app/Contents/Info.plist" <<'PY'
import plistlib
import sys
with open(sys.argv[1], "rb") as file:
    plist = plistlib.load(file)
types = plist.get("CFBundleDocumentTypes", [])
exported = plist.get("UTExportedTypeDeclarations", [])
direct = any("scad3" in row.get("CFBundleTypeExtensions", []) for row in types)
extensions = [row for row in exported if "scad3" in row.get("UTTypeTagSpecification", {}).get("public.filename-extension", [])]
registered = {item for row in types for item in row.get("LSItemContentTypes", [])}
assert direct or any(row.get("UTTypeIdentifier") in registered for row in extensions), "Missing .scad3 association"
PY
}

install "$1"
check 0.4.4
install "$2"
check "$3"
check_association
test -s "$app/Contents/Resources/app.asar"
install "$1"
check 0.4.4
echo "Install, upgrade and rollback passed on macOS."
