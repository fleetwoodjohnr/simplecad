#!/usr/bin/env bash
# SimpleCAD environment bootstrap.
# Creates a project-local venv and installs the geometry kernel + Qt bindings.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"

if [ ! -d "$VENV" ]; then
    echo "==> Creating venv at $VENV"
    python3 -m venv "$VENV"
fi

echo "==> Installing dependencies (this downloads ~700 MB the first time)"
"$VENV/bin/python" -m pip install --upgrade pip wheel >/dev/null
"$VENV/bin/python" -m pip install -r "$ROOT/requirements.txt"

echo "==> Verifying"
"$VENV/bin/python" - <<'PY'
import OCP, PySide6, scipy, numpy
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp
box = BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape()
props = GProp_GProps()
BRepGProp.VolumeProperties_s(box, props)
assert abs(props.Mass() - 6000.0) < 1e-6, props.Mass()
print(f"OCP ok (box volume {props.Mass():.0f} mm3)")
print("PySide6", PySide6.__version__)
print("scipy", scipy.__version__, "numpy", numpy.__version__)
PY
echo "==> Installing desktop entry"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/scalable/apps"
mkdir -p "$APPS" "$ICONS"
install -m 0644 "$ROOT/assets/simplecad.svg" "$ICONS/simplecad.svg"
sed "s|SIMPLECAD_LAUNCHER|$ROOT/bin/simplecad|g" "$ROOT/simplecad.desktop" \
    > "$APPS/simplecad.desktop"
chmod 0644 "$APPS/simplecad.desktop"
update-desktop-database "$APPS" 2>/dev/null || true
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

echo "==> Setup complete."
echo "    Run ./bin/simplecad, or launch SimpleCAD from the app grid."
