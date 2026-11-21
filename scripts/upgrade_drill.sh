#!/usr/bin/env bash
# Install v0.4.x in a clean container, upgrade to Electron, then roll back.
#
#   scripts/upgrade_drill.sh <old .rpm|.deb> <new .rpm|.deb>
#
# .rpm runs in Fedora 44 (dnf upgrade), .deb in Ubuntu 24.04 (apt install).
# Uses Podman by default. Set SIMPLECAD_CONTAINER_ENGINE=docker on Docker hosts.
set -euo pipefail
if [[ $# != 2 ]]; then
  echo "usage: $0 <old package> <new package>" >&2
  exit 2
fi
old=$(realpath "$1")
new=$(realpath "$2")
case "${old##*.}:${new##*.}" in
  rpm:rpm) image=registry.fedoraproject.org/fedora:44 ;;
  deb:deb) image=docker.io/library/ubuntu:24.04 ;;
  *) echo "Choose two packages of the same type (.rpm or .deb)." >&2; exit 2 ;;
esac
kind=${new##*.}
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
fixture="$(dirname "$0")/../app/tests/e2e/fixtures/features.scad3"
cp -- "$old" "$scratch/before.$kind"
cp -- "$new" "$scratch/after.$kind"
cp -- "$fixture" "$scratch/model.scad3"

"${SIMPLECAD_CONTAINER_ENGINE:-podman}" run --rm -i -v "$scratch:/drill:ro,Z" "$image" bash -euo pipefail -s -- "$kind" <<'DRILL'
kind=$1
old="/drill/before.$kind"
new="/drill/after.$kind"

if [[ $kind == rpm ]]; then
  dnf -y -q install util-linux shadow-utils "$old"
  expected_version() { rpm -qp --qf '%{VERSION}-%{RELEASE}' "$1"; }
  installed_version() { rpm -q --qf '%{VERSION}-%{RELEASE}' simplecad; }
  upgrade() { dnf -y -q upgrade "$new"; }
  rollback() { dnf -y -q downgrade "$old"; }
else
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq "$old"
  expected_version() { dpkg-deb -f "$1" Version; }
  installed_version() { dpkg-query -W -f '${Version}' simplecad; }
  upgrade() { apt-get install -y -qq "$new"; }
  rollback() { apt-get install --allow-downgrades -y -qq "$old"; }
fi

useradd -m tester
cd /home/tester
runuser -u tester -- mkdir -p .config/simplecad
install -o tester -g tester -m 0644 /drill/model.scad3 model.scad3
runuser -u tester -- sh -c 'printf "%s\n" "{\"kept\": true}" > .config/simplecad/settings.json'
sha256sum model.scad3 .config/simplecad/settings.json > /tmp/before.sha

verify() {
  test "$(installed_version)" = "$(expected_version "$1")"
  printf '%s: %s\n' "$2" "$(installed_version)"
  runuser -u tester -- /usr/bin/simplecad --self-test
  sha256sum -c /tmp/before.sha
}

verify "$old" initial
upgrade
verify "$new" upgraded
test ! -e /opt/simplecad/_internal
test "$(cat /opt/simplecad/resources/install-kind)" = "$kind"
test -f /usr/share/mime/packages/simplecad-mime.xml
rollback
verify "$old" rolled-back
DRILL
