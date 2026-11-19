#!/usr/bin/env bash
# Upgrade drill: install an old SimpleCAD package in a clean container, upgrade
# it to a new one, and check the command, the self-test, the user's settings
# and that nothing of the old install is left behind.
#
#   scripts/upgrade_drill.sh <old .rpm|.deb> <new .rpm|.deb>
#
# .rpm runs in Fedora 44 (dnf upgrade), .deb in Ubuntu 24.04 (apt install). Needs podman.
set -euo pipefail
old=$(realpath "$1"); new=$(realpath "$2")
[ "$(dirname "$old")" = "$(dirname "$new")" ] || { echo "put both packages in one folder"; exit 2; }
case "$new" in
  *.rpm) image=registry.fedoraproject.org/fedora:44
         install="dnf install -y -q"; upgrade="dnf upgrade -y -q"; version="rpm -q simplecad"
         prepare="dnf install -y -q util-linux shadow-utils" ;;
  *.deb) image=docker.io/library/ubuntu:24.04
         install="apt-get install -y -qq"; upgrade="apt-get install -y -qq"; version="dpkg-query -W simplecad"
         prepare="export DEBIAN_FRONTEND=noninteractive; apt-get update -qq" ;;
  *) echo "a .rpm or .deb, please"; exit 2 ;;
esac
podman run --rm -v "$(dirname "$new"):/drill:Z" "$image" bash -euc "
  $prepare >/dev/null 2>&1
  $install /drill/$(basename "$old") >/dev/null 2>&1
  echo \"before: \$($version) | /usr/bin/simplecad -> \$(readlink /usr/bin/simplecad)\"
  useradd -m tester
  runuser -u tester -- bash -c 'mkdir -p ~/.config/simplecad && echo \"{\\\"kept\\\": true}\" > ~/.config/simplecad/settings.json'
  $upgrade /drill/$(basename "$new") >/dev/null 2>&1
  echo \"after: \$($version) | /usr/bin/simplecad -> \$(readlink /usr/bin/simplecad)\"
  runuser -u tester -- bash -c '/usr/bin/simplecad --self-test 2>&1 | tail -1; cat ~/.config/simplecad/settings.json'
  test ! -e /opt/simplecad/_internal || { echo 'the old Python app is still there'; exit 1; }
  echo 'install-kind:' \$(cat /opt/simplecad/resources/install-kind)
"
